import pytest
from fastapi.testclient import TestClient

from nexus.app import app
from nexus.state import get_state
from nexus.upstream import FakeUpstream


@pytest.fixture
def client(monkeypatch, policies_dir):
    monkeypatch.setenv("NEXUS_KEY_SHOPSCOUT", "sk-shopscout")
    monkeypatch.setenv("NEXUS_KEY_WEALTHWISE", "sk-wealthwise")
    monkeypatch.setenv("NEXUS_KEY_WUWORK", "sk-wuwork")
    monkeypatch.setenv("POLICIES_DIR", str(policies_dir))
    get_state.cache_clear()
    yield TestClient(app)
    get_state.cache_clear()


def _post(client, key, model="zai/glm-4.6"):
    return client.post(
        "/v1/chat/completions",
        json={"model": model, "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {key}"},
    )


def test_pinned_model_is_served_untouched(client):
    r = _post(client, "sk-shopscout")
    assert r.status_code == 200
    assert r.json()["model"] == "zai/glm-4.6"
    assert get_state().ledger.entries()[0].model == "zai/glm-4.6"


def test_fallback_is_reported_in_the_response_and_the_ledger(client):
    get_state().upstream = FakeUpstream(fail_models=frozenset({"zai/glm-4.6"}))
    r = _post(client, "sk-wuwork")
    assert r.status_code == 200
    body = r.json()
    assert body["nexus_fallback"]["from"] == "zai/glm-4.6"
    assert body["nexus_fallback"]["to"] == body["model"]
    assert body["model"] != "zai/glm-4.6"
    entry = get_state().ledger.entries()[0]
    assert entry.fallback_from == "zai/glm-4.6"
    assert entry.model == body["model"]


def test_a_tenant_that_forbids_fallback_gets_503_not_a_weaker_answer(client):
    get_state().upstream = FakeUpstream(fail_models=frozenset({"zai/glm-4.6"}))
    r = _post(client, "sk-wealthwise")
    assert r.status_code == 503
    # Nothing was served and nothing was billed. A failed attempt produces
    # no upstream charge, so recording it would immediately show up as an
    # orphan_entry and turn G2 red.
    assert get_state().ledger.entries() == []


def test_successful_calls_carry_no_fallback_field(client):
    r = _post(client, "sk-shopscout")
    assert "nexus_fallback" not in r.json()


def test_exhausted_fallback_chain_gives_503(client):
    everything = frozenset(
        {"zai/glm-4.6", "zai/glm-4.7", "siliconflow/Qwen/Qwen3-8B",
         "siliconflow/Qwen/Qwen3-235B-A22B", "dashscope/qwen3-235b-a22b",
         "siliconflow/deepseek-ai/DeepSeek-V3"}
    )
    get_state().upstream = FakeUpstream(fail_models=everything)
    assert _post(client, "sk-wuwork").status_code == 503


def test_ledger_still_reconciles_after_a_fallback(client):
    from nexus.ledger.book import reconcile

    get_state().upstream = FakeUpstream(fail_models=frozenset({"zai/glm-4.6"}))
    _post(client, "sk-wuwork")
    state = get_state()
    # The failed first attempt produced no upstream charge and no billed
    # row; the successful second attempt produced exactly one of each.
    assert reconcile(state.ledger.entries(), state.upstream.charges()) == []


def test_a_vetoed_route_reaches_the_routing_log(client, monkeypatch):
    # The unit tests prove RoutingLog stores what it is given. This proves
    # the handler actually gives it the veto -- the half that would silently
    # go missing if someone moved the recording below the raise.
    import nexus.ingress.api as api
    from nexus.policy.routing import RouteDecision

    def _greedy(policy, requested, prices):
        return RouteDecision(
            requested=requested, model="siliconflow/Qwen/Qwen3-8B",
            substituted=True, reason="greedy",
        )

    monkeypatch.setattr(api, "choose", _greedy)
    r = _post(client, "sk-shopscout")
    assert r.status_code == 500
    vetoed = [e for e in get_state().routing.events() if e.vetoed]
    assert vetoed, "the veto never reached the log"
    assert vetoed[0].tenant == "shopscout"

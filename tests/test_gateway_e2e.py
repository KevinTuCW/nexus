import pytest
from fastapi.testclient import TestClient

from nexus.app import app, get_state


@pytest.fixture
def client(monkeypatch, policies_dir):
    monkeypatch.setenv("NEXUS_KEY_SHOPSCOUT", "sk-shopscout-xyz")
    monkeypatch.setenv("POLICIES_DIR", str(policies_dir))
    get_state.cache_clear()
    yield TestClient(app)
    get_state.cache_clear()


def _body(model="zai/glm-4.6"):
    return {"model": model, "messages": [{"role": "user", "content": "hi"}]}


def test_unauthenticated_request_is_401(client):
    r = client.post("/v1/chat/completions", json=_body())
    assert r.status_code == 401


def test_authenticated_request_returns_an_openai_shaped_response(client):
    r = client.post(
        "/v1/chat/completions",
        json=_body(),
        headers={"Authorization": "Bearer sk-shopscout-xyz"},
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["choices"][0]["message"]["content"]
    assert payload["usage"]["prompt_tokens"] > 0


def test_a_served_request_lands_in_the_ledger(client):
    client.post(
        "/v1/chat/completions",
        json=_body(),
        headers={"Authorization": "Bearer sk-shopscout-xyz"},
    )
    entries = get_state().ledger.entries()
    assert len(entries) == 1
    assert entries[0].tenant == "shopscout"
    assert entries[0].model == "zai/glm-4.6"
    assert entries[0].family == "glm"
    assert entries[0].cost_nanousd > 0


def test_ledger_reconciles_against_the_fake_upstream(client):
    from nexus.ledger.book import reconcile

    for _ in range(3):
        client.post(
            "/v1/chat/completions",
            json=_body(),
            headers={"Authorization": "Bearer sk-shopscout-xyz"},
        )
    state = get_state()
    assert reconcile(state.ledger.entries(), state.upstream.charges()) == []


def test_unpriced_model_is_refused_rather_than_billed_at_zero(client):
    # Serving a model we have no price for would write a 0-cost row, and
    # the ledger would reconcile against a fake upstream that also says 0.
    # Refuse at the door instead.
    r = client.post(
        "/v1/chat/completions",
        json=_body(model="some/never-priced"),
        headers={"Authorization": "Bearer sk-shopscout-xyz"},
    )
    assert r.status_code == 400
    assert get_state().ledger.entries() == []

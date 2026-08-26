"""What a failing provider is allowed to do to a diversity group.

The group path reserved a weight family *before* calling the provider and
never looked again. Two consequences, both of them the exact failure G1
exists to prevent:

  - the reserved model fails, the fallback chain serves something else, and
    the something else can be a family another juror already holds. The
    group collapses, and the reservation ledger says it did not;
  - the reservation is consumed even when the call never happened, so a
    group can run out of families on models it never used.

Driven through the gateway, not through `GroupLedger` directly: the bug was
never in `GroupLedger`, it was in nobody telling it what actually got served.
"""

import pytest
from fastapi.testclient import TestClient

from nexus.app import app
from nexus.policy.diversity import GroupLedger
from nexus.registry.families import family_of
from nexus.state import get_state
from nexus.upstream import FakeUpstream


@pytest.fixture
def client(monkeypatch, policies_dir):
    monkeypatch.setenv("NEXUS_KEY_WUWORK", "sk-wuwork")
    monkeypatch.setenv("POLICIES_DIR", str(policies_dir))
    get_state.cache_clear()
    yield TestClient(app)
    get_state.cache_clear()


def _post(client, model, group=None):
    body = {"model": model, "messages": [{"role": "user", "content": "hi"}]}
    if group:
        body["nexus_diversity_group"] = group
    return client.post(
        "/v1/chat/completions", json=body,
        headers={"Authorization": "Bearer sk-wuwork"},
    )


def test_a_fallback_inside_a_group_cannot_repeat_a_family(client):
    state = get_state()
    # Juror one takes the glm family.
    first = _post(client, "zai/glm-4.6", group="jury-f1")
    assert first.status_code == 200, first.text
    taken = family_of(first.json()["model"])

    # Juror two asks for a qwen3 model whose whole family is down, so the
    # only thing left to fall back to is the family juror one holds.
    state.upstream = FakeUpstream(
        fail_models=frozenset(
            {"siliconflow/Qwen/Qwen3-8B", "siliconflow/Qwen/Qwen3-235B-A22B",
             "dashscope/qwen3-235b-a22b", "siliconflow/deepseek-ai/DeepSeek-V3"}
        )
    )
    second = _post(client, "siliconflow/Qwen/Qwen3-8B", group="jury-f1")

    if second.status_code == 200:
        served = family_of(second.json()["model"])
        assert served != taken, (
            f"group jury-f1 served two members from family '{served}'; the "
            "fallback walked around the reservation"
        )
    else:
        # Refusing is the other acceptable outcome, and the honest one: a
        # guarantee that cannot be met must fail loudly rather than degrade.
        assert second.status_code in (409, 503), second.text


def test_a_call_that_was_never_served_consumes_nothing(client):
    state = get_state()
    # The whole glm family is down, so this member gets a 503 and no model.
    state.upstream = FakeUpstream(
        fail_models=frozenset({"zai/glm-4.6", "zai/glm-4.7"})
    )
    failed = _post(client, "zai/glm-4.6", group="jury-f2")
    assert failed.status_code == 503, failed.text

    # The family must still be free. Reserving up front spent it on a call
    # that never happened, and the retry -- of the very same request -- was
    # then refused for repeating a family nobody had used.
    state.upstream = FakeUpstream()
    retry = _post(client, "zai/glm-4.6", group="jury-f2")
    assert retry.status_code == 200, (
        "the glm family was consumed by a call that was never served: " + retry.text
    )
    assert family_of(retry.json()["model"]) == "glm"


def test_a_same_family_fallback_still_occupies_one_seat(client):
    # The control on the rule above. A juror that falls back from glm-4.6 to
    # glm-4.7 has used the glm family exactly once -- not zero times, which
    # would let the next juror sit in it too.
    state = get_state()
    state.upstream = FakeUpstream(fail_models=frozenset({"zai/glm-4.6"}))
    first = _post(client, "zai/glm-4.6", group="jury-f3")
    assert first.status_code == 200, first.text
    assert first.json()["model"] == "zai/glm-4.7"

    state.upstream = FakeUpstream()
    second = _post(client, "zai/glm-4.6", group="jury-f3")
    assert second.status_code == 409, second.text


def test_the_group_ledger_does_not_grow_without_bound():
    # Groups are never explicitly released -- `release()` exists and no
    # caller invokes it. An unbounded dict keyed by a caller-supplied string
    # is a slow leak that reads like a feature until the process is old
    # enough to matter, which is the same reasoning RoutingLog is bounded by.
    ledger = GroupLedger(capacity=4)
    for i in range(50):
        ledger.reserve(f"group-{i}", ["zai/glm-4.6"])
    assert ledger.tracked_groups() <= 4


def test_evicting_an_old_group_does_not_forget_a_live_one():
    # Eviction must be least-recently-used, not arbitrary. A group still
    # being filled in must survive the arrival of unrelated ones, or the
    # bound above would trade a memory leak for a silent diversity failure.
    ledger = GroupLedger(capacity=3)
    ledger.reserve("live", ["zai/glm-4.6"])
    for i in range(2):
        ledger.reserve(f"noise-{i}", ["zai/glm-4.6"])
    ledger.reserve("live", ["siliconflow/Qwen/Qwen3-8B"])
    ledger.reserve("noise-9", ["zai/glm-4.6"])
    from nexus.policy.diversity import DiversityExhausted

    with pytest.raises(DiversityExhausted):
        ledger.reserve("live", ["zai/glm-4.7"])

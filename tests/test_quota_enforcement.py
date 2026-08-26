"""A budget nobody enforces is a number on a dashboard.

`check_budget` existed, was unit-tested, and was read by the console panel --
and was never called on any request path. A tenant could run arbitrarily far
past `budget_nanousd_per_day` while the panel cheerfully rendered the
overshoot next to the word `active`. These tests exist so that cannot come
back: they drive traffic through the gateway rather than calling the policy
function directly, because calling the policy function directly is exactly
what the old test suite did while the gateway ignored it.
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from nexus.app import app
from nexus.ledger.book import Entry
from nexus.ledger.usage import Usage
from nexus.state import get_state


@pytest.fixture
def client(monkeypatch, policies_dir):
    monkeypatch.setenv("NEXUS_KEY_WUWORK", "sk-wuwork")
    monkeypatch.setenv("NEXUS_KEY_HELPMATE", "sk-helpmate")
    monkeypatch.setenv("POLICIES_DIR", str(policies_dir))
    get_state.cache_clear()
    yield TestClient(app)
    get_state.cache_clear()


def _call(client, key="sk-wuwork", model="zai/glm-4.6"):
    return client.post(
        "/v1/chat/completions",
        json={"model": model, "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {key}"},
    )


def _spend(tenant: str, amount: int, *, ts: datetime | None = None) -> Entry:
    return Entry(
        entry_id=f"e-{tenant}-{amount}-{ts}", call_id=f"c-{tenant}-{amount}-{ts}",
        tenant=tenant, workload="default", trace_root=None,
        span_id="s", parent_span_id=None, model="zai/glm-4.6", family="glm",
        usage=Usage(prompt_tokens=1, completion_tokens=1),
        cost_nanousd=amount, status="ok",
        ts=ts or datetime.now(timezone.utc),
    )


def test_a_tenant_over_its_budget_is_refused(client):
    state = get_state()
    budget = state.policies["wuwork"].budget_nanousd_per_day
    state.ledger.record(_spend("wuwork", budget + 1))

    r = _call(client)
    assert r.status_code == 429, r.text
    assert "budget" in r.json()["detail"]


def test_a_tenant_under_its_budget_is_served(client):
    # The control. Without it, an implementation that refuses everything
    # would satisfy the test above.
    state = get_state()
    budget = state.policies["wuwork"].budget_nanousd_per_day
    state.ledger.record(_spend("wuwork", budget // 2))
    assert _call(client).status_code == 200


def test_budget_zero_means_switched_off_not_unlimited(client):
    state = get_state()
    state.policies["wuwork"] = replace(
        state.policies["wuwork"], budget_nanousd_per_day=0
    )
    r = _call(client)
    assert r.status_code == 429, r.text


def test_yesterdays_spend_does_not_count_against_today(client):
    # `budget_nanousd_per_day` says per day. Summing the whole ledger would
    # make the budget a lifetime cap that quietly tightens every day until
    # the tenant is permanently locked out for traffic it ran last month.
    state = get_state()
    budget = state.policies["wuwork"].budget_nanousd_per_day
    yesterday = datetime.now(timezone.utc) - timedelta(days=1, hours=1)
    state.ledger.record(_spend("wuwork", budget * 10, ts=yesterday))
    assert _call(client).status_code == 200


def test_one_tenants_spend_does_not_exhaust_anothers_budget(client):
    state = get_state()
    state.ledger.record(
        _spend("helpmate", state.policies["helpmate"].budget_nanousd_per_day * 10)
    )
    assert _call(client, key="sk-wuwork").status_code == 200
    assert _call(client, key="sk-helpmate", model="glm-4.7").status_code == 429


def test_the_refusal_is_recorded_where_a_person_will_look(client):
    # A tenant asking "why did my traffic stop" must find the answer in the
    # console, not only in a 429 body that was thrown away by an SDK.
    state = get_state()
    state.ledger.record(
        _spend("wuwork", state.policies["wuwork"].budget_nanousd_per_day + 1)
    )
    _call(client)
    panel = client.get(
        "/console/quota", headers={"Authorization": "Bearer sk-wuwork"}
    ).json()
    row = {t["tenant"]: t for t in panel["tenants"]}["wuwork"]
    assert row["state"] == "over_budget"


def test_the_quota_panel_reports_todays_spend_not_all_time(client):
    state = get_state()
    yesterday = datetime.now(timezone.utc) - timedelta(days=1, hours=1)
    state.ledger.record(_spend("wuwork", 7_000, ts=yesterday))
    state.ledger.record(_spend("wuwork", 11, ts=datetime.now(timezone.utc)))
    panel = client.get(
        "/console/quota", headers={"Authorization": "Bearer sk-wuwork"}
    ).json()
    row = {t["tenant"]: t for t in panel["tenants"]}["wuwork"]
    assert row["spent_nanousd"] == 11

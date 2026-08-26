"""Authenticated is not authorised, and the console is where the two got confused.

`/v1/usage` refuses a cross-tenant read without an explicit
`cross_tenant_read` grant, and says so at length. Every console panel then
returned every tenant's data to anyone holding *any* tenant key -- so the
boundary the platform sells could be walked around by asking a different
endpoint in the same application for the same numbers.

The rule is one rule, so it lives in one place (`ingress.authz`) and both
surfaces call it. Two copies of an authorisation rule is two authorisation
rules, and the looser one is the one that decides.
"""

import pytest
from fastapi.testclient import TestClient

from nexus.app import app
from nexus.state import get_state

PANELS = [
    "/console/costs",
    "/console/routing",
    "/console/gates",
    "/console/fallbacks",
    "/console/quota",
]


@pytest.fixture
def client(monkeypatch, policies_dir):
    monkeypatch.setenv("NEXUS_KEY_WUWORK", "sk-wuwork")
    monkeypatch.setenv("NEXUS_KEY_HELPMATE", "sk-helpmate")
    monkeypatch.setenv("NEXUS_KEY_WEALTHWISE", "sk-wealthwise")
    monkeypatch.setenv("POLICIES_DIR", str(policies_dir))
    get_state.cache_clear()
    yield TestClient(app)
    get_state.cache_clear()


HELPMATE = {"Authorization": "Bearer sk-helpmate"}
WUWORK = {"Authorization": "Bearer sk-wuwork"}


def _spend(tenant, amount, call_id):
    from datetime import datetime, timezone

    from nexus.ledger.book import Entry
    from nexus.ledger.usage import Usage

    return Entry(
        entry_id=f"e-{call_id}", call_id=call_id, tenant=tenant,
        workload="default", trace_root=None, span_id=call_id,
        parent_span_id=None, model="zai/glm-4.6", family="glm",
        usage=Usage(prompt_tokens=1, completion_tokens=1),
        cost_nanousd=amount, status="ok", ts=datetime.now(timezone.utc),
        fallback_from="zai/glm-4.7",
    )


@pytest.fixture
def seeded(client):
    state = get_state()
    state.ledger.record(_spend("wealthwise", 999_000, "c-wealthwise"))
    state.ledger.record(_spend("helpmate", 111, "c-helpmate"))
    state.routing.record(
        "wealthwise", "zai/glm-4.6", "zai/glm-4.6", vetoed=False, reason="ok"
    )
    return client


def test_costs_panel_hides_other_tenants(seeded):
    body = seeded.get("/console/costs", headers=HELPMATE).json()
    assert "wealthwise" not in body["by_tenant"]
    assert body["by_tenant"] == {"helpmate": 111}


def test_the_console_agrees_with_the_usage_endpoint(seeded):
    # The defect in one sentence: /v1/usage said 403 and /console/costs said
    # 200 with the same numbers, to the same caller, in the same process.
    denied = seeded.get("/v1/usage?tenants=wealthwise", headers=HELPMATE)
    assert denied.status_code == 403
    panel = seeded.get("/console/costs", headers=HELPMATE).json()
    assert "wealthwise" not in panel["by_tenant"]


def test_routing_panel_hides_other_tenants(seeded):
    body = seeded.get("/console/routing", headers=HELPMATE).json()
    assert [e for e in body["events"] if e["tenant"] != "helpmate"] == []


def test_fallbacks_panel_hides_other_tenants(seeded):
    body = seeded.get("/console/fallbacks", headers=HELPMATE).json()
    assert [e for e in body["events"] if e["tenant"] != "helpmate"] == []


def test_gates_panel_hides_other_tenants(seeded):
    # A tenant's gate command names its repo path and its build tooling.
    # That is not usage data, but it is still somebody else's.
    body = seeded.get("/console/gates", headers=HELPMATE).json()
    assert [row["tenant"] for row in body["tenants"]] == ["helpmate"]


def test_quota_panel_hides_other_tenants(seeded):
    body = seeded.get("/console/quota", headers=HELPMATE).json()
    assert [row["tenant"] for row in body["tenants"]] == ["helpmate"]


def test_the_granted_tenant_still_sees_everything(seeded):
    # The control, and the reason this is scoping rather than a lockout:
    # wuwork holds `cross_tenant_read` for all four incumbents because group
    # finance writes the operations digest. Scoping that reads as an outage
    # would get the scoping reverted.
    body = seeded.get("/console/costs", headers=WUWORK).json()
    assert "wealthwise" in body["by_tenant"]
    assert "helpmate" in body["by_tenant"]


def test_a_console_crossing_is_audited_like_any_other(seeded):
    seeded.get("/console/costs", headers=WUWORK)
    crossings = [r for r in get_state().audit.records() if r.caller == "wuwork"]
    assert crossings, "reading four other tenants' spend left no audit record"


@pytest.mark.parametrize("path", PANELS)
def test_every_panel_still_requires_authentication(client, path):
    assert client.get(path).status_code == 401

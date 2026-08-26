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
    monkeypatch.setenv("POLICIES_DIR", str(policies_dir))
    get_state.cache_clear()
    yield TestClient(app)
    get_state.cache_clear()


H = {"Authorization": "Bearer sk-wuwork"}


@pytest.mark.parametrize("path", PANELS)
def test_every_panel_requires_authentication(client, path):
    # The console reads every tenant's data. An unauthenticated panel is a
    # cross-tenant leak with a nicer font.
    assert client.get(path).status_code == 401


def test_routing_panel_shows_vetoes(client):
    get_state().routing.record(
        "shopscout", "zai/glm-4.6", "siliconflow/Qwen/Qwen3-8B",
        vetoed=True, reason="policy does not permit qwen3",
    )
    body = client.get("/console/routing", headers=H).json()
    vetoed = [e for e in body["events"] if e["vetoed"]]
    assert vetoed and "qwen3" in vetoed[0]["reason"]


def test_gates_panel_marks_tenants_without_a_baseline(client):
    # aura and helpmate have none. "not_covered" is not "pass": nobody
    # checked, and a green cell would say otherwise.
    body = client.get("/console/gates", headers=H).json()
    by_tenant = {row["tenant"]: row["state"] for row in body["tenants"]}
    assert by_tenant["aura"] == "not_covered"
    assert by_tenant["helpmate"] == "not_covered"


def test_gates_panel_marks_tenants_with_a_baseline_differently(client):
    body = client.get("/console/gates", headers=H).json()
    by_tenant = {row["tenant"]: row["state"] for row in body["tenants"]}
    assert by_tenant["wuwork"] != "not_covered"


def test_quota_panel_calls_zero_budget_switched_off(client):
    body = client.get("/console/quota", headers=H).json()
    for row in body["tenants"]:
        if row["budget_nanousd_per_day"] == 0:
            assert row["state"] == "switched_off"


def test_costs_panel_declares_its_unit(client):
    # A number rendered without its unit is a number someone will read as
    # dollars. These are nano-USD; the difference is a factor of a billion.
    assert client.get("/console/costs", headers=H).json()["currency_unit"] == "nanousd"


def test_fallbacks_panel_lists_only_displaced_calls(client):
    from datetime import datetime, timezone

    from nexus.ledger.book import Entry
    from nexus.ledger.usage import Usage

    def _row(call_id, fallback_from):
        return Entry(
            entry_id=f"e-{call_id}", call_id=call_id, tenant="wuwork",
            workload="default", trace_root=None, span_id=call_id,
            parent_span_id=None, model="zai/glm-4.7", family="glm",
            usage=Usage(prompt_tokens=1, completion_tokens=1),
            cost_nanousd=1, status="ok", ts=datetime.now(timezone.utc),
            fallback_from=fallback_from,
        )

    state = get_state()
    state.ledger.record(_row("plain", None))
    state.ledger.record(_row("displaced", "zai/glm-4.6"))
    body = client.get("/console/fallbacks", headers=H).json()
    assert [e["call_id"] for e in body["events"]] == ["displaced"]

import pytest
from fastapi.testclient import TestClient

from nexus.app import app
from nexus.state import get_state


@pytest.fixture
def client(monkeypatch, policies_dir):
    monkeypatch.setenv("NEXUS_KEY_WUWORK", "sk-wuwork")
    monkeypatch.setenv("NEXUS_KEY_SHOPSCOUT", "sk-shopscout")
    monkeypatch.setenv("POLICIES_DIR", str(policies_dir))
    get_state.cache_clear()
    yield TestClient(app)
    get_state.cache_clear()


def _get(client, key, tenants=None):
    q = f"?tenants={tenants}" if tenants else ""
    return client.get(f"/v1/usage{q}", headers={"Authorization": f"Bearer {key}"})


def test_reading_your_own_usage_needs_no_grant(client):
    assert _get(client, "sk-shopscout").status_code == 200


def test_an_unauthorised_crossing_is_refused(client):
    # shopscout has no cross_tenant_read grant at all.
    assert _get(client, "sk-shopscout", tenants="wealthwise").status_code == 403


def test_the_authorised_tenant_can_read_the_others(client):
    r = _get(client, "sk-wuwork", tenants="helpmate,shopscout")
    assert r.status_code == 200
    assert set(r.json()["by_tenant"]) <= {"helpmate", "shopscout"}


def test_partial_authorisation_refuses_the_whole_request(client):
    # Returning just the authorised half would tell the caller that the
    # other tenant spent nothing. A group digest missing one business line
    # gets taken into a meeting; an error gets fixed.
    r = _get(client, "sk-wuwork", tenants="helpmate,ghost-tenant")
    assert r.status_code == 403
    assert "ghost-tenant" in r.json()["detail"]


def test_a_successful_crossing_is_audited(client):
    _get(client, "sk-wuwork", tenants="helpmate")
    (rec,) = get_state().audit.records()
    assert rec.caller == "wuwork"
    assert rec.targets == ("helpmate",)
    assert rec.denied is False


def test_a_refused_crossing_is_audited(client):
    _get(client, "sk-shopscout", tenants="wealthwise")
    (rec,) = get_state().audit.records()
    assert rec.caller == "shopscout"
    assert rec.denied is True


def test_reading_your_own_usage_is_not_audited_as_a_crossing(client):
    # Every tenant reads its own usage constantly. Logging that as a
    # boundary crossing buries the handful of real ones.
    _get(client, "sk-shopscout")
    assert get_state().audit.records() == []


def test_naming_yourself_among_the_targets_is_not_a_crossing(client):
    # `tenants=shopscout` from shopscout is the same request as no
    # parameter at all. Treating it as a crossing would make a tenant need
    # a grant to read itself.
    _get(client, "sk-shopscout", tenants="shopscout")
    assert get_state().audit.records() == []


def test_unauthenticated_is_401(client):
    assert client.get("/v1/usage").status_code == 401

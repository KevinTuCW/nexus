"""The control plane end to end: tightening hot, loosening by proposal only.

These use a real database because the whole subject is what survives a write.
They use their *own* tables -- created `LIKE <real> INCLUDING ALL` so they
cannot drift from the real schema -- because a test that revokes credentials
or switches tenants off must never reach the tables the running gateway
authenticates against.
"""

import os

import pytest
from fastapi.testclient import TestClient

from nexus.state import get_state

DSN = os.environ.get("DATABASE_URL", "")
pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not DSN, reason="no DATABASE_URL in the shell"),
]

TABLES = {
    "policy_override": "policy_override_cptest",
    "tenant_budget": "tenant_budget_cptest",
    "admin_action": "admin_action_cptest",
    "tenant_key": "tenant_key_cptest",
    "admin_account": "admin_account_cptest",
    "admin_session": "admin_session_cptest",
}

PASSWORD = "correct-horse-battery"


@pytest.fixture
def client(monkeypatch, policies_dir):
    import psycopg

    from nexus.admin import api as admin_api
    from nexus.admin.accounts import AccountStore
    from nexus.admin.store import ControlPlaneStore, TenantKeyStore
    from nexus.app import create_app

    with psycopg.connect(DSN) as conn:
        conn.execute(f"DROP TABLE IF EXISTS {TABLES['admin_session']}")
        for real, test in TABLES.items():
            conn.execute(f"DROP TABLE IF EXISTS {test}")
            conn.execute(f"CREATE TABLE {test} (LIKE {real} INCLUDING ALL)")

    monkeypatch.setenv("POLICIES_DIR", str(policies_dir))
    monkeypatch.setenv("DATABASE_URL", DSN)
    monkeypatch.setenv("NEXUS_KEY_WUWORK", "sk-wuwork")
    # Cookies over the test client are plain http and it stores whatever it
    # is given, but Secure would still be wrong to assert here.
    monkeypatch.setenv("ADMIN_COOKIE_SECURE", "false")

    accounts = AccountStore(
        DSN, TABLES["admin_account"], TABLES["admin_session"]
    )
    monkeypatch.setattr(admin_api, "_accounts", lambda: accounts)
    accounts.create("kevin", PASSWORD, "rw")
    accounts.create("ops-li", PASSWORD, "ro")
    accounts.create("mei", PASSWORD, "rw")

    def stores():
        return (
            ControlPlaneStore(
                DSN,
                TABLES["policy_override"],
                TABLES["tenant_budget"],
                TABLES["admin_action"],
            ),
            TenantKeyStore(DSN, TABLES["tenant_key"]),
        )

    # Only `_stores` is redirected. Stubbing the store *methods* would also
    # neuter `_refresh()`, which is precisely what these tests exist to
    # exercise -- the hot effect would silently become a no-op and every
    # assertion below would be testing nothing.
    monkeypatch.setattr(admin_api, "_stores", stores)
    get_state.cache_clear()
    c = TestClient(create_app())
    # Log in as the read-write administrator; tests that need the read-only
    # one call `login(c, "ops-li")` to swap the session.
    login(c, "kevin")
    yield c
    get_state.cache_clear()
    with psycopg.connect(DSN) as conn:
        for test in TABLES.values():
            conn.execute(f"DROP TABLE IF EXISTS {test}")


def login(client, username, password=PASSWORD):
    """Log in and let the test client carry the session cookie."""
    return client.post(
        "/admin/login", json={"username": username, "password": password}
    )


def _tenant(client, name="wuwork"):
    rows = client.get("/admin/tenants").json()["tenants"]
    return next(t for t in rows if t["tenant"] == name)


# --- tightening is hot ---------------------------------------------------


def test_switching_a_tenant_off_takes_effect_immediately(client):
    assert _tenant(client)["enabled"] is True
    r = client.post(
        "/admin/overrides",
                json={
            "tenant": "wuwork",
            "field": "enabled",
            "removed_value": "true",
            "reason": "incident 4711",
        },
    )
    assert r.status_code == 200
    assert r.json()["effective"]["enabled"] is False
    # The data plane refuses on the next request, without a restart.
    assert (
        client.get("/console/costs", headers={"Authorization": "Bearer sk-wuwork"})
        .status_code
        == 403
    )


def test_removing_a_substitution_narrows_the_effective_policy(client):
    before = _tenant(client)["effective"]["models"]["siliconflow/Qwen/Qwen3-8B"]
    assert "qwen3" in before
    client.post(
        "/admin/overrides",
                json={
            "tenant": "wuwork",
            "field": "substitutable_to",
            "model": "siliconflow/Qwen/Qwen3-8B",
            "removed_value": "qwen3",
            "reason": "pin during migration",
        },
    )
    after = _tenant(client)["effective"]["models"]["siliconflow/Qwen/Qwen3-8B"]
    assert after == []
    # Declared is untouched: the panel can still say what was taken away.
    assert "qwen3" in _tenant(client)["declared"]["models"][
        "siliconflow/Qwen/Qwen3-8B"
    ]


def test_a_tightening_without_a_reason_is_refused(client):
    r = client.post(
        "/admin/overrides",
                json={"tenant": "wuwork", "field": "enabled", "reason": "  "},
    )
    assert r.status_code == 400
    assert "reason" in r.json()["detail"]


def test_the_override_layer_refuses_a_field_it_cannot_narrow(client):
    r = client.post(
        "/admin/overrides",
                json={
            "tenant": "wuwork",
            "field": "budget_nanousd_per_day",
            "removed_value": "1",
            "reason": "x",
        },
    )
    assert r.status_code == 400
    assert "proposals" in r.json()["detail"]


# --- every hot change has an inverse -------------------------------------


def test_lifting_an_override_restores_the_declared_value(client):
    r = client.post(
        "/admin/overrides",
                json={
            "tenant": "wuwork",
            "field": "cross_tenant_read",
            "removed_value": "aura",
            "reason": "temporary",
        },
    )
    oid = r.json()["override_id"]
    assert "aura" not in _tenant(client)["effective"]["cross_tenant_read"]

    client.post(f"/admin/overrides/{oid}/lift")
    assert "aura" in _tenant(client)["effective"]["cross_tenant_read"]


# --- hot effect must not cost the routing log ----------------------------


def test_applying_an_override_does_not_wipe_the_routing_log(client):
    state = get_state()
    state.routing.record(
        tenant="wuwork", requested="a", routed="b", vetoed=True, reason="G1"
    )
    assert len(state.routing.events()) == 1

    client.post(
        "/admin/overrides",
                json={
            "tenant": "wuwork", "field": "allow_fallback",
            "removed_value": "true", "reason": "x",
        },
    )
    # cache_clear() would have rebuilt State and discarded this -- the record
    # of what G1 vetoed, and the only reason the console's routing panel has
    # anything to show.
    assert len(get_state().routing.events()) == 1


# --- budget: direction decides, not danger -------------------------------


def test_lowering_a_budget_never_needs_approval(client):
    r = client.post(
        "/admin/budget",
                json={"tenant": "wuwork", "budget_nanousd_per_day": 0, "reason": "stop"},
    )
    assert r.status_code == 200
    assert r.json()["effective"]["budget_nanousd_per_day"] == 0
    assert r.json()["approved_by"] is None


def test_a_large_raise_needs_a_second_administrator(client):
    current = _tenant(client)["effective"]["budget_nanousd_per_day"]
    r = client.post(
        "/admin/budget",
                json={
            "tenant": "wuwork",
            "budget_nanousd_per_day": current * 10,
            "reason": "campaign",
        },
    )
    assert r.status_code == 403
    assert "复核" in r.json()["detail"]


def test_the_approver_cannot_be_the_proposer(client):
    current = _tenant(client)["effective"]["budget_nanousd_per_day"]
    r = client.post(
        "/admin/budget",
                json={
            "tenant": "wuwork",
            "budget_nanousd_per_day": current * 10,
            "reason": "campaign",
            "approved_by": "kevin",
        },
    )
    assert r.status_code == 403


def test_a_large_raise_goes_through_with_a_second_administrator(client):
    current = _tenant(client)["effective"]["budget_nanousd_per_day"]
    r = client.post(
        "/admin/budget",
                json={
            "tenant": "wuwork",
            "budget_nanousd_per_day": current * 10,
            "reason": "campaign",
            "approved_by": "mei",
        },
    )
    assert r.status_code == 200
    assert r.json()["approved_by"] == "mei"


def test_a_small_raise_needs_no_approval(client):
    current = _tenant(client)["effective"]["budget_nanousd_per_day"]
    r = client.post(
        "/admin/budget",
                json={
            "tenant": "wuwork",
            "budget_nanousd_per_day": current + 1,
            "reason": "tweak",
        },
    )
    assert r.status_code == 200


# --- concurrency ---------------------------------------------------------


def test_a_stale_version_is_refused(client):
    stale = _tenant(client)["version"]
    client.post(
        "/admin/overrides",
                json={
            "tenant": "wuwork", "field": "allow_fallback",
            "removed_value": "true", "reason": "first",
        },
    )
    r = client.post(
        "/admin/overrides",
                json={
            "tenant": "wuwork", "field": "enabled", "removed_value": "true",
            "reason": "second", "version": stale,
        },
    )
    assert r.status_code == 409


def test_the_version_spans_both_mutable_tables(client):
    # Looking at only one table lets the person editing the other believe
    # they hold current state.
    before = _tenant(client)["version"]
    client.post(
        "/admin/budget",
                json={"tenant": "wuwork", "budget_nanousd_per_day": 1, "reason": "x"},
    )
    assert _tenant(client)["version"] != before


# --- credentials ---------------------------------------------------------


def test_an_issued_key_works_immediately_and_is_shown_once(client):
    r = client.post(
        "/admin/keys", json={"tenant": "wuwork", "label": "prod"}
    )
    assert r.status_code == 200
    plaintext = r.json()["api_key"]

    # Hot: usable without a restart.
    assert (
        client.get("/console/costs", headers={"Authorization": f"Bearer {plaintext}"})
        .status_code
        == 200
    )
    # And never retrievable again.
    listing = client.get("/admin/keys").json()
    assert plaintext not in str(listing)


def test_revoking_a_key_takes_effect_immediately(client):
    issued = client.post(
        "/admin/keys", json={"tenant": "wuwork", "label": "temp"}
    ).json()
    client.post(f"/admin/keys/{issued['key_id']}/revoke")
    assert (
        client.get(
            "/console/costs", headers={"Authorization": f"Bearer {issued['api_key']}"}
        ).status_code
        == 401
    )


# --- loosening has no write path -----------------------------------------


def test_a_proposal_returns_a_diff_and_writes_nothing(client):
    before = _tenant(client)["declared"]["cross_tenant_read"]
    r = client.post(
        "/admin/proposals",
                json={"tenant": "helpmate", "field": "cross_tenant_read", "value": "wuwork"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "+cross_tenant_read" in body["diff"] or "wuwork" in body["diff"]
    assert "policies/helpmate.yaml" in body["diff"]
    # Nothing moved.
    assert _tenant(client)["declared"]["cross_tenant_read"] == before
    assert _tenant(client, "helpmate")["effective"]["cross_tenant_read"] == []


def test_a_proposal_reports_no_evidence_rather_than_a_pass(client):
    # An empty ledger has not approved anything. This repository already
    # refuses to print `passed` for that case in nexus.eval.
    r = client.post(
        "/admin/proposals",
                json={
            "tenant": "wuwork", "field": "substitutable_to",
            "model": "zai/glm-4.6", "value": "qwen3",
        },
    )
    assert r.json()["gates"]["verdict"] in {"no_evidence", "clean", "would_violate"}


def test_a_readonly_administrator_may_look_but_not_touch(client):
    login(client, "ops-li")
    assert client.get("/admin/tenants").status_code == 200
    r = client.post(
        "/admin/overrides",
        json={
            "tenant": "wuwork", "field": "enabled",
            "removed_value": "true", "reason": "x",
        },
    )
    assert r.status_code == 403
    assert "read-only" in r.json()["detail"]


# --- audit ---------------------------------------------------------------


def test_every_change_is_attributed_to_a_person(client):
    client.post(
        "/admin/overrides",
                json={
            "tenant": "wuwork", "field": "enabled",
            "removed_value": "true", "reason": "incident",
        },
    )
    actions = client.get("/admin/actions").json()["actions"]
    assert actions
    assert actions[0]["actor"] == "kevin"
    assert actions[0]["action"] == "override.add.enabled"
    assert actions[0]["target"] == "wuwork"


def test_the_audit_log_never_contains_a_credential(client):
    r = client.post(
        "/admin/keys", json={"tenant": "wuwork", "label": "prod"}
    )
    plaintext = r.json()["api_key"]
    actions = client.get("/admin/actions").json()["actions"]
    assert plaintext not in str(actions)
    assert any(a["action"] == "key.issue" for a in actions)


# --- drift ---------------------------------------------------------------


def test_an_orphan_override_is_listed_not_cleaned(client):
    # An override naming a value the declared policy no longer has does
    # nothing, yet still reads as "in force". Listing it is the point;
    # cleaning it silently would hide both possible causes.
    client.post(
        "/admin/overrides",
                json={
            "tenant": "wuwork", "field": "cross_tenant_read",
            "removed_value": "a-tenant-that-left", "reason": "stale",
        },
    )
    body = client.get("/admin/tenants").json()
    assert len(body["orphans"]) == 1
    assert "a-tenant-that-left" in body["orphans"][0]["removed_value"]
    # Still present, not deleted.
    assert any(
        o["removed_value"] == "a-tenant-that-left"
        for t in body["tenants"]
        for o in t["overrides_in_force"]
    )

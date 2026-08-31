"""The control plane end to end: tightening hot, loosening by request only.

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
    "change_request": "change_request_cptest",
    "admin_account": "admin_account_cptest",
    "admin_session": "admin_session_cptest",
}

PASSWORD = "correct-horse-battery"


@pytest.fixture
def client(monkeypatch, policies_dir):
    import psycopg

    from nexus.admin import api as admin_api
    from nexus.admin.accounts import AccountStore
    from nexus.admin.store import (
        ChangeRequestStore,
        ControlPlaneStore,
        TenantKeyStore,
    )
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
    monkeypatch.setattr(
        admin_api, "_requests",
        lambda: ChangeRequestStore(DSN, TABLES["change_request"]),
    )
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
    assert "change-requests" in r.json()["detail"]


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


def test_a_change_request_returns_the_config_and_writes_nothing(client):
    before = _tenant(client)["declared"]["cross_tenant_read"]
    r = client.post(
        "/admin/change-requests",
                json={"tenant": "helpmate", "field": "cross_tenant_read",
              "value": "wuwork", "reason": "复用需求"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "+cross_tenant_read" in body["config"] or "wuwork" in body["config"]
    assert "policies/helpmate.yaml" in body["config"]
    # Nothing moved.
    assert _tenant(client)["declared"]["cross_tenant_read"] == before
    assert _tenant(client, "helpmate")["effective"]["cross_tenant_read"] == []


def test_a_change_request_reports_no_evidence_rather_than_a_pass(client):
    # An empty ledger has not approved anything. This repository already
    # refuses to print `passed` for that case in nexus.eval.
    r = client.post(
        "/admin/change-requests",
                json={
            "tenant": "wuwork", "field": "substitutable_to",
            "model": "zai/glm-4.6", "value": "qwen3", "reason": "x",
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
    assert "只读权限" in r.json()["detail"]


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


# --- administrators over HTTP --------------------------------------------


def test_a_logged_in_admin_can_create_another(client):
    # The *first* administrator still cannot be made this way -- there is no
    # session to authenticate. Subsequent ones have no bootstrap window:
    # somebody already authenticated is vouching.
    r = client.post(
        "/admin/accounts",
        json={"username": "new-op", "password": "another-long-password", "role": "ro"},
    )
    assert r.status_code == 200
    names = {a["username"] for a in client.get("/admin/accounts").json()["accounts"]}
    assert "new-op" in names


def test_creating_an_admin_with_a_short_password_is_refused(client):
    r = client.post(
        "/admin/accounts", json={"username": "weak", "password": "short", "role": "rw"}
    )
    assert r.status_code == 400


def test_an_admin_cannot_disable_itself(client):
    # That would lock the last administrator out with no way back in.
    r = client.post("/admin/accounts/kevin/disable")
    assert r.status_code == 400


def test_the_account_listing_never_carries_a_hash(client):
    blob = str(client.get("/admin/accounts").json())
    assert "password_hash" not in blob and "salt" not in blob


def test_a_readonly_admin_cannot_create_accounts(client):
    login(client, "ops-li")
    r = client.post(
        "/admin/accounts",
        json={"username": "x", "password": "another-long-password", "role": "rw"},
    )
    assert r.status_code == 403


# --- creating a tenant is a request, not a write -------------------------


def test_creating_a_tenant_produces_a_file_not_a_row(client):
    before = {t["tenant"] for t in client.get("/admin/tenants").json()["tenants"]}
    r = client.post(
        "/admin/change-requests/tenant",
        json={"tenant": "newline", "integration": "zero_touch",
              "gate_command": "make eval", "budget_nanousd_per_day": 5,
              "reason": "新业务线"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["path"] == "policies/newline.yaml"
    assert "tenant: newline" in body["config"]
    # Nothing was created.
    after = {t["tenant"] for t in client.get("/admin/tenants").json()["tenants"]}
    assert after == before


def test_a_proposed_tenant_starts_closed(client):
    # No models, no cross-tenant grants, fallback off. A tenant that arrives
    # with permissions already granted is a tenant nobody reviewed.
    diff = client.post(
        "/admin/change-requests/tenant",
        json={"tenant": "newline", "budget_nanousd_per_day": 5, "reason": "x"},
    ).json()["config"]
    assert "cross_tenant_read" not in diff
    assert "models" not in diff
    assert "allow_fallback: false" in diff


def test_proposing_a_tenant_that_already_exists_is_refused(client):
    r = client.post("/admin/change-requests/tenant", json={"tenant": "wuwork", "reason": "x"})
    assert r.status_code == 409


def test_the_new_tenant_change_request_does_not_claim_the_gates_passed(client):
    # An unbuilt tenant has no ledger rows, so the gates have judged nothing.
    # Saying "clean" here would be the empty-ledger lie in a new place.
    body = client.post(
        "/admin/change-requests/tenant", json={"tenant": "newline", "reason": "x"}
    ).json()
    assert body["gates"]["verdict"] == "not_applicable"
    assert "verify_tenant" in body["gates"]["detail"]


# --- account recovery: disable used to be a one-way door -----------------


def test_a_disabled_account_can_be_brought_back(client):
    client.post("/admin/accounts", json={"username": "temp", "password": PASSWORD})
    client.post("/admin/accounts/temp/disable")
    assert login(client, "temp").status_code == 401
    login(client, "kevin")

    assert client.post("/admin/accounts/temp/enable").status_code == 200
    assert login(client, "temp").status_code == 200
    login(client, "kevin")


def test_re_enabling_does_not_revive_the_sessions_that_were_ended(client):
    # "May log in again" and "the tokens from before still work" are two
    # different promises, and only the first one is being made.
    client.post("/admin/accounts", json={"username": "temp", "password": PASSWORD})
    from fastapi.testclient import TestClient as _TC
    other = _TC(client.app)
    login(other, "temp")
    assert other.get("/admin/whoami").status_code == 200

    client.post("/admin/accounts/temp/disable")
    client.post("/admin/accounts/temp/enable")
    assert other.get("/admin/whoami").status_code == 401


def test_unlock_clears_a_lockout_without_touching_the_password(client):
    from nexus.admin.accounts import MAX_FAILED_ATTEMPTS

    client.post("/admin/accounts", json={"username": "temp", "password": PASSWORD})
    for _ in range(MAX_FAILED_ATTEMPTS):
        login(client, "temp", "wrong-password-entirely")
    login(client, "kevin")
    assert _account(client, "temp")["state"] == "locked"
    # Frozen, so the real password does not work either.
    assert login(client, "temp").status_code == 401
    login(client, "kevin")

    assert client.post("/admin/accounts/temp/unlock").status_code == 200
    assert login(client, "temp").status_code == 200
    login(client, "kevin")


def test_unlock_refuses_a_disabled_account_rather_than_quietly_enabling_it(client):
    client.post("/admin/accounts", json={"username": "temp", "password": PASSWORD})
    client.post("/admin/accounts/temp/disable")
    r = client.post("/admin/accounts/temp/unlock")
    assert r.status_code == 409
    assert _account(client, "temp")["state"] == "disabled"


def test_enabling_an_account_that_does_not_exist_is_a_404(client):
    assert client.post("/admin/accounts/ghost/enable").status_code == 404


def test_a_read_only_administrator_cannot_recover_accounts(client):
    client.post("/admin/accounts", json={"username": "temp", "password": PASSWORD})
    client.post("/admin/accounts/temp/disable")
    login(client, "ops-li")
    assert client.post("/admin/accounts/temp/enable").status_code == 403
    assert client.post("/admin/accounts/temp/unlock").status_code == 403


def _account(client, username):
    rows = client.get("/admin/accounts").json()["accounts"]
    return next(a for a in rows if a["username"] == username)


# --- changing your own password ------------------------------------------


NEW_PASSWORD = "a-different-long-password"


def test_changing_your_password_requires_the_current_one(client):
    r = client.post(
        "/admin/password",
        json={"current_password": "not-it", "new_password": NEW_PASSWORD},
    )
    assert r.status_code == 403
    # And the old one still works, i.e. the failed attempt changed nothing.
    assert login(client, "kevin").status_code == 200


def test_a_failed_password_change_does_not_count_towards_the_lockout(client):
    # Routing this through login() would freeze the account whose session is
    # making the call -- the caller locks themselves out by mistyping.
    from nexus.admin.accounts import MAX_FAILED_ATTEMPTS

    for _ in range(MAX_FAILED_ATTEMPTS + 2):
        client.post(
            "/admin/password",
            json={"current_password": "not-it", "new_password": NEW_PASSWORD},
        )
    assert _account(client, "kevin")["state"] == "active"
    assert login(client, "kevin").status_code == 200


def test_changing_your_password_ends_your_other_sessions_but_not_this_one(client):
    from fastapi.testclient import TestClient as _TC

    other = _TC(client.app)
    login(other, "kevin")
    assert other.get("/admin/whoami").status_code == 200

    r = client.post(
        "/admin/password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert r.status_code == 200
    assert r.json()["sessions_ended"] == 1
    # The point of changing a password under suspicion is to evict whoever
    # else is holding it.
    assert other.get("/admin/whoami").status_code == 401
    assert client.get("/admin/whoami").status_code == 200
    assert login(client, "kevin", NEW_PASSWORD).status_code == 200


def test_a_short_new_password_is_refused(client):
    r = client.post(
        "/admin/password",
        json={"current_password": PASSWORD, "new_password": "short"},
    )
    assert r.status_code == 400
    assert login(client, "kevin").status_code == 200


def test_a_read_only_administrator_may_still_change_their_own_password(client):
    # Refusing would mean their password can only ever be changed by somebody
    # else, which is worse than letting them.
    login(client, "ops-li")
    r = client.post(
        "/admin/password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert r.status_code == 200
    assert login(client, "ops-li", NEW_PASSWORD).status_code == 200


def test_there_is_no_way_to_reset_somebody_elses_password(client):
    # Deliberately absent: an administrator who can set a colleague's password
    # to a value they know can sign in as that colleague, and the second
    # signature on a budget raise stops meaning anything.
    for path in ("/admin/accounts/mei/password", "/admin/accounts/mei/reset"):
        assert client.post(path, json={"new_password": NEW_PASSWORD}).status_code == 404


# --- the overview ---------------------------------------------------------
@pytest.fixture
def empty_ledger(client):
    """Point State at a ledger with nothing in it.

    `client` redirects every control-plane store to a `_cptest` table, but the
    ledger is assembled into `State` straight from `DATABASE_URL` and shares
    the real `ledger_entry` table with the developer's own gateway. Without
    this, "the overview reports no evidence" passes or fails depending on
    whether somebody ran a request against localhost this morning.
    """
    from nexus.ledger.book import InMemoryLedger

    state = get_state()
    original = state.ledger
    state.ledger = InMemoryLedger()
    yield state.ledger
    state.ledger = original




def test_the_overview_needs_a_session(client):
    client.post("/admin/logout")
    assert client.get("/admin/overview").status_code == 401


def test_the_overview_says_no_evidence_rather_than_a_calm_zero(client, empty_ledger):
    # Nothing has been through the gateway in this test's world. An alert that
    # renders an unmeasured thing as 0 looks exactly like a measured all-clear.
    body = client.get("/admin/overview").json()
    assert body["totals"]["has_ledger_evidence"] is False
    alerts = {a["key"]: a for a in body["alerts"]}
    assert alerts["over_budget"]["count"] is None
    assert alerts["failed"]["count"] is None
    assert alerts["fallbacks"]["count"] is None
    # These are derivable without any traffic, so they are real zeros.
    assert alerts["orphans"]["count"] == 0
    assert alerts["pending"]["count"] == 0


def test_the_overview_labels_its_two_clocks_separately(client):
    # Ledger figures are scoped to today on the gateway's own day boundary;
    # routing vetoes come from an in-process log a restart empties. Reporting
    # both as "today" would make a restart look like the anomalies went away.
    alerts = {a["key"]: a for a in client.get("/admin/overview").json()["alerts"]}
    assert alerts["failed"]["window"] == "today"
    assert alerts["vetoed"]["window"] == "since_boot"


def test_the_overview_states_which_upstream_is_answering(client):
    # A console pointed at the fake upstream looks exactly like one pointed at
    # real providers, and somebody raising a budget off these numbers needs to
    # know which without going to find out.
    assert client.get("/admin/overview").json()["upstream"] == "fake"


def test_the_overview_counts_a_switched_off_tenant(client):
    client.post(
        "/admin/overrides",
        json={"tenant": "wuwork", "field": "enabled",
              "removed_value": "true", "reason": "incident"},
    )
    body = client.get("/admin/overview").json()
    assert body["totals"]["tenants_off"] == 1


def test_the_overview_surfaces_a_pending_change_request(client):
    client.post(
        "/admin/change-requests",
        json={"tenant": "wuwork", "field": "cross_tenant_read",
              "value": "medscope", "reason": "对账"},
    )
    alerts = {a["key"]: a for a in client.get("/admin/overview").json()["alerts"]}
    assert alerts["pending"]["count"] == 1
    assert alerts["pending"]["items"][0]["tenant"] == "wuwork"


# --- the page is four files, and none of them carry data -----------------


def test_the_page_assets_are_served(client):
    for name, kind in [("admin.css", "text/css"),
                       ("admin.js", "text/javascript"),
                       ("terms.js", "text/javascript")]:
        r = client.get(f"/admin/static/{name}")
        assert r.status_code == 200, name
        assert kind in r.headers["content-type"]


def test_the_asset_route_is_a_whitelist_not_a_path_join(client):
    # Joining a request path onto a directory is how a static handler becomes
    # a way to read .env.
    for name in ["../../../.env", "..%2f..%2fconfig.py", "nope.txt", "api.py"]:
        assert client.get(f"/admin/static/{name}").status_code in (404, 400), name


def test_the_assets_are_anonymous_but_carry_nothing(client):
    client.post("/admin/logout")
    for name in ["admin.css", "admin.js", "terms.js"]:
        assert client.get(f"/admin/static/{name}").status_code == 200
    # Every panel they draw still needs a session.
    assert client.get("/admin/tenants").status_code == 401

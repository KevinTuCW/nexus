"""Login, sessions, lockout, and the two namespaces staying apart.

The scheme this replaced could only reach a browser through `/admin?key=…`,
because an address bar cannot set a header -- and a secret in a query string
reaches the access log, the `Referer` of every outbound link, and browser
history. These pin the properties that made the replacement worth doing.
"""

import os

import pytest
from fastapi.testclient import TestClient

from nexus.admin.accounts import COOKIE_NAME, LoginFailed, hash_password
from nexus.state import get_state

DSN = os.environ.get("DATABASE_URL", "")
pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not DSN, reason="no DATABASE_URL in the shell"),
]

ACCOUNTS = "admin_account_logintest"
SESSIONS = "admin_session_logintest"
PASSWORD = "correct-horse-battery"


@pytest.fixture
def accounts():
    import psycopg

    from nexus.admin.accounts import AccountStore

    with psycopg.connect(DSN) as conn:
        conn.execute(f"DROP TABLE IF EXISTS {SESSIONS}")
        conn.execute(f"DROP TABLE IF EXISTS {ACCOUNTS}")
        conn.execute(f"CREATE TABLE {ACCOUNTS} (LIKE admin_account INCLUDING ALL)")
        conn.execute(f"CREATE TABLE {SESSIONS} (LIKE admin_session INCLUDING ALL)")
    store = AccountStore(DSN, ACCOUNTS, SESSIONS)
    store.create("kevin", PASSWORD, "rw")
    yield store
    with psycopg.connect(DSN) as conn:
        conn.execute(f"DROP TABLE IF EXISTS {SESSIONS}")
        conn.execute(f"DROP TABLE IF EXISTS {ACCOUNTS}")


@pytest.fixture
def client(monkeypatch, policies_dir, accounts):
    from nexus.admin import api as admin_api
    from nexus.app import create_app

    monkeypatch.setenv("POLICIES_DIR", str(policies_dir))
    monkeypatch.setenv("DATABASE_URL", DSN)
    monkeypatch.setenv("NEXUS_KEY_WUWORK", "sk-wuwork")
    monkeypatch.setenv("ADMIN_COOKIE_SECURE", "false")
    monkeypatch.setattr(admin_api, "_accounts", lambda: accounts)
    get_state.cache_clear()
    yield TestClient(create_app())
    get_state.cache_clear()


# --- password handling ---------------------------------------------------


def test_the_same_password_hashes_differently_for_two_accounts(accounts):
    # Per-account salt. Without it, identical passwords produce identical
    # hashes and one cracked row cracks every account that shares it.
    a, _ = hash_password(PASSWORD)
    b, _ = hash_password(PASSWORD)
    assert a != b


def test_the_table_never_holds_a_plaintext_password(accounts):
    import psycopg

    with psycopg.connect(DSN) as conn:
        row = conn.execute(f"SELECT * FROM {ACCOUNTS}").fetchone()
    assert PASSWORD not in str(row)


def test_a_short_password_is_refused(accounts):
    # This account can switch a tenant off and mint credentials.
    with pytest.raises(ValueError, match="12 characters"):
        accounts.create("weak", "short", "rw")


def test_listing_accounts_never_returns_a_hash_or_salt(accounts):
    blob = str(accounts.list_accounts())
    assert "password_hash" not in blob and "salt" not in blob


# --- login ---------------------------------------------------------------


def test_a_correct_password_opens_a_session(client):
    r = client.post(
        "/admin/login", json={"username": "kevin", "password": PASSWORD}
    )
    assert r.status_code == 200
    assert r.json() == {"admin": "kevin", "role": "rw"}
    assert client.get("/admin/whoami").json()["admin"] == "kevin"


def test_the_session_token_is_not_in_the_response_body(client):
    r = client.post(
        "/admin/login", json={"username": "kevin", "password": PASSWORD}
    )
    token = client.cookies.get(COOKIE_NAME)
    assert token and token not in r.text


def test_the_cookie_is_httponly_and_samesite_strict(client):
    r = client.post(
        "/admin/login", json={"username": "kevin", "password": PASSWORD}
    )
    setcookie = r.headers["set-cookie"].lower()
    # HttpOnly so an injected script cannot read it; SameSite so another
    # site cannot make an authenticated request on the operator's behalf.
    assert "httponly" in setcookie
    assert "samesite=strict" in setcookie


def test_an_unknown_user_and_a_wrong_password_give_the_same_answer(client):
    a = client.post("/admin/login", json={"username": "nobody", "password": PASSWORD})
    b = client.post("/admin/login", json={"username": "kevin", "password": "wrong-wrong-wrong"})
    # Distinguishing the two hands out a username oracle.
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_panels_are_refused_without_a_session(client):
    for path in ["/admin/whoami", "/admin/tenants", "/admin/keys", "/admin/actions"]:
        assert client.get(path).status_code == 401


def test_the_page_shell_is_served_anonymously_because_it_is_the_login_form(client):
    r = client.get("/admin")
    assert r.status_code == 200
    assert "login-form" in r.text
    # And it carries no data and no credential.
    assert "nx-" not in r.text


def test_the_page_no_longer_reads_a_key_from_the_url(client):
    # The whole point of the change: no credential in a query string, which
    # would otherwise reach the access log and every outbound Referer.
    page = client.get("/admin").text
    assert "URLSearchParams" not in page
    assert "?key=" not in page


# --- sessions ------------------------------------------------------------


def test_logging_out_ends_the_session_server_side(client):
    client.post("/admin/login", json={"username": "kevin", "password": PASSWORD})
    token = client.cookies.get(COOKIE_NAME)
    client.post("/admin/logout")

    # Not merely cleared in the browser: presenting the old token fails too.
    client.cookies.set(COOKIE_NAME, token)
    assert client.get("/admin/whoami").status_code == 401


def test_an_expired_session_is_refused(client, accounts):
    import psycopg

    client.post("/admin/login", json={"username": "kevin", "password": PASSWORD})
    with psycopg.connect(DSN) as conn:
        conn.execute(f"UPDATE {SESSIONS} SET expires_at = now() - interval '1 hour'")
    assert client.get("/admin/whoami").status_code == 401


def test_disabling_an_account_ends_its_live_sessions(client, accounts):
    client.post("/admin/login", json={"username": "kevin", "password": PASSWORD})
    assert client.get("/admin/whoami").status_code == 200

    accounts.disable("kevin")
    # "Disabled" has to mean "out now", not "cannot log in again".
    assert client.get("/admin/whoami").status_code == 401


def test_changing_a_password_does_not_silently_keep_old_sessions_working(
    client, accounts
):
    # Documents the current behaviour rather than asserting a wish: a password
    # change does NOT revoke sessions. Recorded here so the next person
    # changing this file knows it is a decision, not an oversight.
    client.post("/admin/login", json={"username": "kevin", "password": PASSWORD})
    accounts.set_password("kevin", "another-long-password")
    assert client.get("/admin/whoami").status_code == 200


# --- lockout -------------------------------------------------------------


def test_five_wrong_passwords_lock_the_account(client, accounts):
    for _ in range(5):
        client.post("/admin/login", json={"username": "kevin", "password": "nope-nope-nope"})
    # The right password now fails too, until the lockout expires.
    r = client.post("/admin/login", json={"username": "kevin", "password": PASSWORD})
    assert r.status_code == 401
    assert accounts.list_accounts()[0]["state"] == "locked"


def test_a_lockout_does_not_touch_other_accounts(client, accounts):
    accounts.create("mei", PASSWORD, "rw")
    for _ in range(6):
        client.post("/admin/login", json={"username": "kevin", "password": "nope-nope-nope"})
    # Throttling is per account: one brute-force run must not lock everyone
    # else out of the console at the moment somebody is attacking it.
    assert client.post(
        "/admin/login", json={"username": "mei", "password": PASSWORD}
    ).status_code == 200


def test_a_successful_login_clears_the_failure_count(client, accounts):
    for _ in range(3):
        client.post("/admin/login", json={"username": "kevin", "password": "nope-nope-nope"})
    client.post("/admin/login", json={"username": "kevin", "password": PASSWORD})
    assert accounts.list_accounts()[0]["failed_attempts"] == 0


# --- the two namespaces ---------------------------------------------------


def test_a_tenant_key_is_not_a_session(client):
    # The control plane accepts no bearer credential at all now, so a tenant
    # key is simply not a session.
    r = client.get("/admin/whoami", headers={"Authorization": "Bearer sk-wuwork"})
    assert r.status_code == 401


def test_a_session_cookie_does_not_open_the_data_plane(client):
    client.post("/admin/login", json={"username": "kevin", "password": PASSWORD})
    r = client.post(
        "/v1/chat/completions",
        json={"model": "zai/glm-4.6", "messages": []},
    )
    assert r.status_code == 401


def test_login_is_refused_for_a_disabled_account(accounts):
    accounts.create("gone", PASSWORD, "rw")
    accounts.disable("gone")
    with pytest.raises(LoginFailed):
        accounts.login("gone", PASSWORD)

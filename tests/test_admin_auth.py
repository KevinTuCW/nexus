import pytest
from fastapi.testclient import TestClient

from nexus.ingress.admin_auth import (
    AdminAuthError,
    authenticate_admin,
    build_admin_index,
)
from nexus.ingress.auth import key_digest
from nexus.state import get_state


@pytest.fixture
def admin_app(monkeypatch, policies_dir, tmp_path):
    """An app with the control plane mounted.

    `database_url` is what decides mounting, not what the store connects to;
    Phase 4a's admin endpoints touch no table, so a DSN that is merely
    non-empty is enough and no Postgres is needed to run these.
    """
    from nexus.app import create_app

    monkeypatch.setenv("POLICIES_DIR", str(policies_dir))
    monkeypatch.setenv("NEXUS_KEY_WUWORK", "sk-wuwork")
    monkeypatch.setenv("NEXUS_ADMIN_KEYS", "kevin:nx-admin-rw,ops-li:ro:nx-admin-ro")
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused/mounting-only")
    monkeypatch.setattr(
        "nexus.admin.store.TenantKeyStore.active_digests", lambda self: {}
    )
    get_state.cache_clear()
    yield TestClient(create_app())
    get_state.cache_clear()


# --- parsing and startup refusals ---


def test_a_readonly_administrator_is_parsed_as_such():
    index = build_admin_index("kevin:nx-a,ops-li:ro:nx-b")
    assert index[key_digest("nx-a")].role == "rw"
    assert index[key_digest("nx-b")].role == "ro"
    assert index[key_digest("nx-b")].may_write is False


def test_blank_input_yields_no_administrators():
    # Absent, not permissive: no administrators means the control plane is
    # not mounted at all.
    assert build_admin_index("") == {}
    assert build_admin_index("   ") == {}


def test_two_administrators_sharing_a_key_refuse_to_start():
    # An audit trail that cannot tell two people apart is not an audit trail.
    with pytest.raises(ValueError, match="share a key"):
        build_admin_index("kevin:nx-same,ops-li:nx-same")


def test_an_administrator_sharing_a_tenant_key_refuses_to_start():
    with pytest.raises(ValueError, match="shares a key with tenant"):
        build_admin_index(
            "kevin:sk-wuwork", tenant_index={key_digest("sk-wuwork"): "wuwork"}
        )


def test_a_malformed_entry_refuses_to_start():
    with pytest.raises(ValueError, match="malformed"):
        build_admin_index("no-key-here")
    with pytest.raises(ValueError, match="malformed"):
        build_admin_index("kevin:")


def test_the_index_holds_no_plaintext():
    index = build_admin_index("kevin:nx-secret")
    assert "nx-secret" not in index
    assert key_digest("nx-secret") in index


def test_an_unknown_credential_is_refused():
    with pytest.raises(AdminAuthError):
        authenticate_admin("Bearer nx-nobody", build_admin_index("kevin:nx-a"))
    with pytest.raises(AdminAuthError):
        authenticate_admin("", build_admin_index("kevin:nx-a"))


# --- the two namespaces do not meet ---


def test_an_administrator_is_named_never_just_admin(admin_app):
    body = admin_app.get(
        "/admin/whoami", headers={"Authorization": "Bearer nx-admin-rw"}
    ).json()
    assert body == {"admin": "kevin", "role": "rw"}


def test_a_readonly_administrator_is_labelled_as_such(admin_app):
    body = admin_app.get(
        "/admin/whoami", headers={"Authorization": "Bearer nx-admin-ro"}
    ).json()
    assert body["role"] == "ro"


def test_a_tenant_key_cannot_enter_the_control_plane(admin_app):
    # 403, not 401: the caller proved who they are and the answer is still
    # no. A data-plane credential never carries administrative power.
    r = admin_app.get("/admin/whoami", headers={"Authorization": "Bearer sk-wuwork"})
    assert r.status_code == 403
    assert "tenant credential" in r.json()["detail"]


def test_an_unknown_credential_gets_401_not_403(admin_app):
    r = admin_app.get("/admin/whoami", headers={"Authorization": "Bearer nx-nobody"})
    assert r.status_code == 401


def test_an_admin_key_cannot_reach_the_data_plane(admin_app):
    r = admin_app.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer nx-admin-rw"},
        json={"model": "zai/glm-4.6", "messages": []},
    )
    assert r.status_code == 401

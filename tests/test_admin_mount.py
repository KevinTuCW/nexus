import pytest
from fastapi.testclient import TestClient

from nexus.state import get_state


@pytest.fixture(autouse=True)
def clean_state():
    get_state.cache_clear()
    yield
    get_state.cache_clear()


def _client(monkeypatch, policies_dir, *, admins: str, database_url: str):
    from nexus.app import create_app

    monkeypatch.setenv("POLICIES_DIR", str(policies_dir))
    monkeypatch.setenv("NEXUS_ADMIN_KEYS", admins)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setattr(
        "nexus.admin.store.TenantKeyStore.active_digests", lambda self: {}
    )
    return TestClient(create_app())


def test_admin_is_not_mounted_without_administrators(monkeypatch, policies_dir):
    # 404, not 401. An endpoint that answers 401 tells a scanner it exists
    # and is worth a dictionary; 404 says there is nothing here, which is
    # true -- nobody could have acted on it anyway.
    client = _client(
        monkeypatch, policies_dir, admins="", database_url="postgresql://unused/x"
    )
    assert client.get("/admin/whoami").status_code == 404
    assert client.get("/admin").status_code == 404


def test_admin_is_not_mounted_without_a_database(monkeypatch, policies_dir):
    # A control plane whose changes die at the next restart is a lie. Same
    # judgement this repo already makes about an empty ledger not being a
    # pass: a missing prerequisite is not something to degrade around.
    client = _client(monkeypatch, policies_dir, admins="kevin:nx-a", database_url="")
    assert client.get("/admin/whoami").status_code == 404


def test_admin_is_mounted_when_both_prerequisites_are_present(
    monkeypatch, policies_dir
):
    client = _client(
        monkeypatch,
        policies_dir,
        admins="kevin:nx-a",
        database_url="postgresql://unused/x",
    )
    r = client.get("/admin/whoami", headers={"Authorization": "Bearer nx-a"})
    assert r.status_code == 200
    assert r.json()["admin"] == "kevin"


def test_the_data_plane_is_unaffected_by_the_control_plane_being_absent(
    monkeypatch, policies_dir
):
    # The gateway must not depend on the control plane existing.
    client = _client(monkeypatch, policies_dir, admins="", database_url="")
    assert client.get("/health").json() == {"status": "ok"}


def test_the_page_shell_authenticates_unlike_the_console(monkeypatch, policies_dir):
    # The console's shell is public because every panel it fetches is not,
    # and a page that renders nothing without a key leaks nothing. This page
    # will carry write controls, so it authenticates from the start.
    client = _client(
        monkeypatch,
        policies_dir,
        admins="kevin:nx-a",
        database_url="postgresql://unused/x",
    )
    assert client.get("/admin").status_code == 401
    assert client.get("/admin", headers={"Authorization": "Bearer nx-a"}).status_code == 200

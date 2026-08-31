"""`enabled` is enforced, and enforced in one place.

The field arrived with the effective-policy layer as something nothing
checked. A control written, tested and drawn into the architecture diagram
but wired to no real path is the exact shape of all four findings from this
repository's last architecture review.
"""

import pytest
from fastapi.testclient import TestClient

from nexus.app import create_app
from nexus.registry.effective import Override, compose
from nexus.state import get_state

H = {"Authorization": "Bearer sk-wuwork"}


@pytest.fixture
def client(monkeypatch, policies_dir):
    monkeypatch.setenv("POLICIES_DIR", str(policies_dir))
    monkeypatch.setenv("NEXUS_KEY_WUWORK", "sk-wuwork")
    get_state.cache_clear()
    yield TestClient(create_app())
    get_state.cache_clear()


def _switch_off(tenant: str) -> None:
    """Compose the tenant off, exactly as the control plane will."""
    state = get_state()
    state.policies[tenant] = compose(
        state.declared[tenant],
        [Override(tenant=tenant, field="enabled", removed_value="true")],
    )


def test_a_disabled_tenant_is_refused_on_chat_completions(client):
    client.get("/health")  # force state assembly
    _switch_off("wuwork")
    r = client.post(
        "/v1/chat/completions",
        headers=H,
        json={"model": "zai/glm-4.6", "messages": [{"role": "user", "content": "x"}]},
    )
    # 403, not 401: the credential is valid and was recognised. Answering 401
    # would send an operator hunting for a key problem that does not exist.
    assert r.status_code == 403
    assert "switched off" in r.json()["detail"]


def test_a_disabled_tenant_is_refused_on_embeddings(client):
    client.get("/health")
    _switch_off("wuwork")
    r = client.post(
        "/v1/embeddings", headers=H, json={"model": "zai/glm-4.6", "input": "x"}
    )
    assert r.status_code == 403


def test_a_disabled_tenant_is_refused_on_rerank(client):
    client.get("/health")
    _switch_off("wuwork")
    r = client.post(
        "/v1/rerank", headers=H, json={"query": "x", "documents": ["a"]}
    )
    assert r.status_code == 403


def test_a_disabled_tenant_is_refused_on_usage(client):
    client.get("/health")
    _switch_off("wuwork")
    assert client.get("/v1/usage", headers=H).status_code == 403


def test_a_disabled_tenant_is_refused_on_the_console(client):
    client.get("/health")
    _switch_off("wuwork")
    assert client.get("/console/costs", headers=H).status_code == 403


def test_an_enabled_tenant_is_unaffected(client):
    # The same five surfaces, with nobody switched off, answer as before.
    assert client.get("/console/costs", headers=H).status_code == 200
    assert client.get("/v1/usage", headers=H).status_code == 200


def test_every_data_plane_surface_goes_through_the_one_chokepoint():
    # Asserts the binding. Five surfaces each repeating the rule is five
    # rules, and they will disagree the first time one of them is edited --
    # which is not hypothetical here: the authorisation rule was copied, was
    # real in /v1/usage, and was absent in the console.
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "src" / "nexus"
    surfaces = [
        root / "ingress" / "api.py",
        root / "ingress" / "passthrough.py",
        root / "ingress" / "usage_api.py",
        root / "console" / "api.py",
    ]
    for path in surfaces:
        text = path.read_text()
        assert "resolve_caller(" in text, f"{path.name} bypasses the chokepoint"
        # `authenticate` may only be reached through caller.py.
        assert "authenticate(" not in text, f"{path.name} authenticates directly"

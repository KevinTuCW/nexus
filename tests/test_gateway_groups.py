import pytest
from fastapi.testclient import TestClient

from nexus.app import app
from nexus.registry.families import family_of
from nexus.state import get_state


@pytest.fixture
def client(monkeypatch, policies_dir):
    monkeypatch.setenv("NEXUS_KEY_WUWORK", "sk-wuwork")
    monkeypatch.setenv("NEXUS_KEY_SHOPSCOUT", "sk-shopscout")
    monkeypatch.setenv("POLICIES_DIR", str(policies_dir))
    get_state.cache_clear()
    yield TestClient(app)
    get_state.cache_clear()


def _post(client, key, model, group=None):
    body = {"model": model, "messages": [{"role": "user", "content": "hi"}]}
    if group:
        body["nexus_diversity_group"] = group
    return client.post(
        "/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"}
    )


def test_group_members_come_from_different_families(client):
    a = _post(client, "sk-wuwork", "zai/glm-4.6", group="jury-1")
    b = _post(client, "sk-wuwork", "siliconflow/Qwen/Qwen3-8B", group="jury-1")
    assert a.status_code == 200, a.text
    assert b.status_code == 200, b.text
    assert family_of(a.json()["model"]) != family_of(b.json()["model"])


def test_exhausting_a_group_fails_loudly(client):
    _post(client, "sk-wuwork", "zai/glm-4.6", group="jury-2")
    r = _post(client, "sk-wuwork", "zai/glm-4.7", group="jury-2")
    assert r.status_code == 409
    assert "jury-2" in r.json()["detail"]


def test_zero_touch_tenants_cannot_use_groups(client):
    # Group membership is a native-integration feature. Accepting it from a
    # zero-touch tenant would imply nexus knows something about that
    # tenant's call structure that it demonstrably does not.
    r = _post(client, "sk-shopscout", "zai/glm-4.6", group="jury-3")
    assert r.status_code == 400


def test_requests_without_a_group_are_unaffected(client):
    for _ in range(3):
        assert _post(client, "sk-wuwork", "zai/glm-4.6").status_code == 200

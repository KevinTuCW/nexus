import pytest
from fastapi.testclient import TestClient

from nexus.app import app
from nexus.ingress.passthrough import PASSTHROUGH_PATHS
from nexus.state import get_state


@pytest.fixture
def client(monkeypatch, policies_dir):
    monkeypatch.setenv("NEXUS_KEY_HELPMATE", "sk-helpmate")
    monkeypatch.setenv("POLICIES_DIR", str(policies_dir))
    get_state.cache_clear()
    yield TestClient(app)
    get_state.cache_clear()


def test_rerank_is_on_the_whitelist():
    # helpmate calls {embed_base_url}/rerank, which is a SiliconFlow
    # extension and not part of the OpenAI-compatible surface. "OpenAI
    # compatible" does not mean "every endpoint compatible", and a tenant
    # integrated zero-touch cannot be asked to stop calling it.
    assert "/rerank" in PASSTHROUGH_PATHS


def test_rerank_requires_authentication(client):
    r = client.post("/rerank", json={"query": "q", "documents": ["a"]})
    assert r.status_code == 401


def test_rerank_is_forwarded_and_answered(client):
    r = client.post(
        "/rerank",
        json={"query": "q", "documents": ["a", "b"]},
        headers={"Authorization": "Bearer sk-helpmate"},
    )
    assert r.status_code == 200
    assert "results" in r.json()


def test_passthrough_writes_no_ledger_row(client):
    # A stated blank, not an oversight. Rerank is not priced per token, so
    # billing it through the token ledger would mean inventing a number --
    # and an invented number in the ledger is worse than a documented gap,
    # because reconciliation would then confirm it.
    client.post(
        "/rerank",
        json={"query": "q", "documents": ["a"]},
        headers={"Authorization": "Bearer sk-helpmate"},
    )
    assert get_state().ledger.entries() == []


def test_arbitrary_paths_are_not_proxied(client):
    r = client.post(
        "/v1/internal/admin",
        json={},
        headers={"Authorization": "Bearer sk-helpmate"},
    )
    assert r.status_code == 404

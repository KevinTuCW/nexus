import os

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


def test_provider_response_is_passed_through_unchanged(client, monkeypatch):
    # Superseded the P2a version of this test, which asserted the shape of a
    # local stub -- once the stub became a real forward, it was asserting a
    # behaviour that no longer exists. What matters now is that nexus adds
    # nothing and drops nothing: helpmate parses this body, and a reranker's
    # score fields are not something a gateway should be reshaping.
    provider_body = {
        "id": "rr-1",
        "results": [
            {"index": 1, "relevance_score": 0.81, "document": {"text": "b"}},
            {"index": 0, "relevance_score": 0.12, "document": {"text": "a"}},
        ],
        "tokens": {"input_tokens": 42},
    }

    class _R:
        status_code = 200

        @staticmethod
        def json():
            return provider_body

    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-provider")
    monkeypatch.setattr(
        "nexus.ingress.passthrough._post", lambda **kw: _R()
    )
    r = client.post(
        "/rerank",
        json={"query": "q", "documents": ["a", "b"]},
        headers={"Authorization": "Bearer sk-helpmate"},
    )
    assert r.status_code == 200
    assert r.json() == provider_body


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


def test_rerank_forwards_to_the_configured_provider(client, monkeypatch):
    seen = {}

    def _fake_post(url, json, headers, timeout):
        seen["url"] = url
        seen["json"] = json
        seen["auth"] = headers.get("Authorization")

        class _R:
            status_code = 200

            @staticmethod
            def json():
                return {"results": [{"index": 0, "relevance_score": 0.9}]}

        return _R()

    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-provider")
    monkeypatch.setattr("nexus.ingress.passthrough._post", _fake_post)
    r = client.post(
        "/rerank",
        json={"model": "Qwen/Qwen3-Reranker-8B", "query": "q", "documents": ["a"]},
        headers={"Authorization": "Bearer sk-helpmate"},
    )
    assert r.status_code == 200
    assert seen["url"].endswith("/rerank")
    # The tenant's key must not be forwarded upstream: tenants authenticate
    # to nexus, nexus authenticates to the provider. Passing the tenant key
    # through would make every tenant a provider credential holder.
    assert seen["auth"] == "Bearer sk-provider"
    assert seen["json"]["query"] == "q"
    # The model is the tenant's choice, forwarded verbatim. helpmate picked
    # Qwen3-Reranker-8B; nexus has no opinion and must not substitute here --
    # rerank has no price table, so a swap would be both unbilled and
    # invisible.
    assert seen["json"]["model"] == "Qwen/Qwen3-Reranker-8B"


def test_provider_failure_surfaces_as_502(client, monkeypatch):
    def _boom(url, json, headers, timeout):
        raise TimeoutError("connect timeout")

    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-provider")
    monkeypatch.setattr("nexus.ingress.passthrough._post", _boom)
    r = client.post(
        "/rerank",
        json={"query": "q", "documents": ["a"]},
        headers={"Authorization": "Bearer sk-helpmate"},
    )
    assert r.status_code == 502


def test_rerank_without_a_provider_key_is_503_not_a_fabricated_answer(client, monkeypatch):
    # Returning a plausible-looking ranking here would be the worst outcome:
    # helpmate's retrieval would silently degrade to whatever order the
    # documents happened to arrive in.
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    r = client.post(
        "/rerank",
        json={"query": "q", "documents": ["a"]},
        headers={"Authorization": "Bearer sk-helpmate"},
    )
    assert r.status_code == 503


@pytest.mark.live
@pytest.mark.skipif(
    not os.environ.get("SILICONFLOW_API_KEY"),
    reason="no SILICONFLOW_API_KEY; live rerank skipped",
)
def test_live_rerank_reaches_siliconflow(client):
    # helpmate reranks with Qwen/Qwen3-Reranker-8B. Proving this endpoint
    # really answers matters more than most live smokes: `/rerank` is not in
    # the OpenAI surface, so nothing else in the suite would notice if the
    # path or payload shape were wrong.
    r = client.post(
        "/rerank",
        json={
            "model": "Qwen/Qwen3-Reranker-8B",
            "query": "how do I reset the controller",
            "documents": ["hold the power button for ten seconds", "battery specifications"],
        },
        headers={"Authorization": "Bearer sk-helpmate"},
    )
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert len(results) == 2
    # The reset instructions must outrank the battery datasheet.
    best = max(results, key=lambda x: x["relevance_score"])
    assert best["index"] == 0

import os

import pytest

from nexus.ledger.usage import Usage
from nexus.upstream import PRICES, UpstreamUnavailable
from nexus.upstream_litellm import LiteLLMUpstream


class _StubCompletion:
    """Stands in for LiteLLM's response object.

    Deliberately returns a *raw OpenAI-shaped payload*, because that is what
    the adapter has to reconcile against. Building the stub out of the
    adapter's own normalised type would make the test agree with whatever
    the adapter does.
    """

    def __init__(self, payload):
        self._payload = payload

    def model_dump(self):
        return self._payload


def _payload(prompt=1000, completion=200, cached=400):
    return {
        "choices": [{"message": {"role": "assistant", "content": "hi there"}}],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "prompt_tokens_details": {"cached_tokens": cached},
        },
    }


def test_completion_content_and_normalised_usage(monkeypatch):
    monkeypatch.setenv("GLM_API_KEY", "k")
    up = LiteLLMUpstream(
        completion_fn=lambda **kw: _StubCompletion(_payload()), stream_fn=None
    )
    c = up.complete("call-1", "zai/glm-4.6", [{"role": "user", "content": "hi"}])
    assert c.content == "hi there"
    # 1000 total minus 400 cached: the OpenAI convention, applied once.
    assert c.usage == Usage(
        prompt_tokens=600, completion_tokens=200, cache_read_tokens=400
    )


def test_charge_is_derived_from_the_raw_payload_not_the_normalised_usage(monkeypatch):
    # The one thing that keeps reconciliation from being the ledger checking
    # itself. If both sides went through from_openai_payload, a bug in that
    # function would cancel out on both sides and G2 would stay green while
    # every invoice drifted.
    monkeypatch.setenv("GLM_API_KEY", "k")
    up = LiteLLMUpstream(
        completion_fn=lambda **kw: _StubCompletion(_payload()), stream_fn=None
    )
    up.complete("call-2", "zai/glm-4.6", [{"role": "user", "content": "hi"}])
    charge = up.charges()[0]
    p = PRICES["zai/glm-4.6"]
    assert charge.cost_nanousd == 600 * p.prompt + 200 * p.completion + 400 * p.cache_read
    assert charge.call_id == "call-2"


def test_missing_api_key_fails_loudly(monkeypatch):
    monkeypatch.delenv("GLM_API_KEY", raising=False)
    up = LiteLLMUpstream(
        completion_fn=lambda **kw: _StubCompletion(_payload()), stream_fn=None
    )
    with pytest.raises(RuntimeError) as exc:
        up.complete("call-3", "zai/glm-4.6", [{"role": "user", "content": "hi"}])
    assert "GLM_API_KEY" in str(exc.value)


def test_provider_errors_become_upstream_unavailable(monkeypatch):
    monkeypatch.setenv("GLM_API_KEY", "k")

    def _boom(**kw):
        raise TimeoutError("connect timeout")

    up = LiteLLMUpstream(completion_fn=_boom, stream_fn=None)
    with pytest.raises(UpstreamUnavailable):
        up.complete("call-4", "zai/glm-4.6", [{"role": "user", "content": "hi"}])


def test_unregistered_model_is_refused(monkeypatch):
    up = LiteLLMUpstream(
        completion_fn=lambda **kw: _StubCompletion(_payload()), stream_fn=None
    )
    with pytest.raises(KeyError):
        up.complete("call-5", "some/never-registered", [])


@pytest.mark.skipif(
    not os.environ.get("GLM_API_KEY"), reason="no GLM_API_KEY; live smoke skipped"
)
def test_live_smoke_reaches_a_real_provider():
    # Skipped without a key, never silently passed. The point is to prove
    # the transport table is right; correctness of the answer is not tested.
    up = LiteLLMUpstream()
    c = up.complete("live-1", "zai/glm-4.6", [{"role": "user", "content": "say hi"}])
    assert c.content
    assert up.charges()[0].cost_nanousd > 0

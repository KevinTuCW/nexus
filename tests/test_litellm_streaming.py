import os
from datetime import datetime, timezone

import pytest

from nexus.ingress.streaming import metered_stream
from nexus.ledger.book import Entry, reconcile
from nexus.upstream import PRICES
from nexus.upstream_litellm import LiteLLMUpstream


class _Chunk:
    def __init__(self, content=None, usage=None):
        self._content = content
        self._usage = usage

    def model_dump(self):
        payload = {"choices": [{"delta": {}}]}
        if self._content is not None:
            payload["choices"][0]["delta"]["content"] = self._content
        if self._usage is not None:
            payload["usage"] = self._usage
        return payload


def _stream_fn(chunks):
    def _fn(**kwargs):
        return iter(chunks)

    return _fn


def test_clean_stream_books_the_providers_usage_frame(monkeypatch):
    monkeypatch.setenv("GLM_API_KEY", "k")
    chunks = [
        _Chunk("a"),
        _Chunk("b"),
        _Chunk(usage={"prompt_tokens": 10, "completion_tokens": 2}),
    ]
    up = LiteLLMUpstream(completion_fn=None, stream_fn=_stream_fn(chunks))
    out = list(up.stream("call-1", "zai/glm-4.6", [{"role": "user", "content": "hi"}]))
    assert out == ["a", "b"]
    p = PRICES["zai/glm-4.6"]
    # The provider's own figure, not our chunk count: 10 prompt tokens is
    # something only the provider can know.
    assert up.charges()[0].cost_nanousd == 10 * p.prompt + 2 * p.completion


def test_abandoned_stream_books_what_was_emitted(monkeypatch):
    # No usage frame ever arrives. The provider still charged for the part
    # it produced, and nexus books its best available figure -- the chunk
    # count, explicitly a lower bound.
    monkeypatch.setenv("GLM_API_KEY", "k")
    chunks = [
        _Chunk("a"),
        _Chunk("b"),
        _Chunk("c"),
        _Chunk(usage={"prompt_tokens": 10, "completion_tokens": 3}),
    ]
    up = LiteLLMUpstream(completion_fn=None, stream_fn=_stream_fn(chunks))
    gen = up.stream("call-2", "zai/glm-4.6", [{"role": "user", "content": "hi"}])
    assert next(gen) == "a"
    gen.close()
    assert len(up.charges()) == 1
    assert up.charges()[0].cost_nanousd > 0


def test_metered_stream_over_a_litellm_upstream_reconciles(monkeypatch):
    monkeypatch.setenv("GLM_API_KEY", "k")
    chunks = [
        _Chunk("a"),
        _Chunk("b"),
        _Chunk(usage={"prompt_tokens": 2, "completion_tokens": 2}),
    ]
    up = LiteLLMUpstream(completion_fn=None, stream_fn=_stream_fn(chunks))
    settled: list = []
    list(metered_stream(up, "call-3", "zai/glm-4.6", [{"content": "hi"}], settled.append))
    entry = Entry(
        entry_id="e-1",
        call_id="call-3",
        tenant="wuwork",
        workload="default",
        trace_root=None,
        span_id="s-1",
        parent_span_id=None,
        model="zai/glm-4.6",
        family="glm",
        usage=settled[0].usage,
        cost_nanousd=settled[0].cost_nanousd,
        status=settled[0].status,
        ts=datetime.now(timezone.utc),
    )
    assert reconcile([entry], up.charges()) == []


@pytest.mark.skipif(
    not os.environ.get("GLM_API_KEY"), reason="no GLM_API_KEY; live smoke skipped"
)
def test_live_stream_smoke():
    up = LiteLLMUpstream()
    chunks = list(
        up.stream("live-2", "zai/glm-4.6", [{"role": "user", "content": "count to three"}])
    )
    assert chunks
    assert up.charges()[0].cost_nanousd > 0

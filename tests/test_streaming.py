import pytest

from nexus.ingress.streaming import metered_stream
from nexus.upstream import FakeUpstream


def _consume(gen, stop_after=None):
    out = []
    for i, chunk in enumerate(gen):
        out.append(chunk)
        if stop_after is not None and i + 1 >= stop_after:
            gen.close()
            break
    return out


def test_full_stream_settles_every_chunk():
    up = FakeUpstream()
    settled: list = []
    chunks = _consume(
        metered_stream(up, "call-1", "zai/glm-4.6", [{"content": "hi"}], settled.append)
    )
    assert len(chunks) == 5
    assert len(settled) == 1
    assert settled[0].usage.completion_tokens == 5
    assert settled[0].status == "ok"


def test_abandoned_stream_still_settles_what_was_emitted():
    # The case gate G2 was designed around. The client walks away after two
    # chunks; the provider still charged for two chunks. A ledger that
    # settles only on clean completion is short by exactly the traffic that
    # goes wrong most often.
    up = FakeUpstream()
    settled: list = []
    chunks = _consume(
        metered_stream(up, "call-2", "zai/glm-4.6", [{"content": "hi"}], settled.append),
        stop_after=2,
    )
    assert len(chunks) == 2
    assert len(settled) == 1
    assert settled[0].usage.completion_tokens == 2
    assert settled[0].status == "aborted"


def test_abandoned_stream_reconciles_against_the_upstream():
    # Not just "a row exists" -- the row must agree with what the provider
    # booked for the same partial stream.
    up = FakeUpstream()
    settled: list = []
    _consume(
        metered_stream(up, "call-3", "zai/glm-4.6", [{"content": "hi"}], settled.append),
        stop_after=2,
    )
    charged = {c.call_id: c.cost_nanousd for c in up.charges()}
    assert charged["call-3"] == settled[0].cost_nanousd


def test_prompt_tokens_are_counted_once_not_per_chunk():
    # Re-counting the prompt on every chunk would inflate a long-context
    # call by the number of chunks -- large, plausible-looking, and only
    # visible against the upstream.
    up = FakeUpstream()
    settled: list = []
    _consume(
        metered_stream(up, "call-4", "zai/glm-4.6", [{"content": "hello"}], settled.append)
    )
    assert settled[0].usage.prompt_tokens == 5  # len("hello")

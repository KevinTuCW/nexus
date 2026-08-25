import pytest

from nexus.ledger.session import MeterSession, meter
from nexus.ledger.usage import Usage
from nexus.money import Price

PRICE = Price(prompt=600, completion=2200)


def _sink() -> list:
    return []


def test_normal_completion_settles_once():
    settled = _sink()
    with meter("call-1", PRICE, settled.append) as s:
        s.observe(Usage(prompt_tokens=10, completion_tokens=5))
    assert len(settled) == 1
    assert settled[0].usage.completion_tokens == 5
    assert settled[0].status == "ok"


def test_aborted_stream_still_settles_with_partial_tokens():
    # The whole reason settlement lives in `finally`. When a client hangs up
    # mid-stream the upstream vendor still bills every token it generated.
    # Settling only on the happy path means nexus eats that cost and the
    # invoice will never reconcile -- and the shortfall grows with traffic,
    # so it looks like a pricing error rather than a missing code path.
    settled = _sink()
    with pytest.raises(RuntimeError):
        with meter("call-2", PRICE, settled.append) as s:
            s.observe(Usage(prompt_tokens=10, completion_tokens=0))
            for i in range(3):
                s.add_completion_tokens(1)
            raise RuntimeError("client disconnected")
    assert len(settled) == 1
    assert settled[0].usage.completion_tokens == 3
    assert settled[0].status == "aborted"
    assert settled[0].cost_nanousd == 10 * 600 + 3 * 2200


def test_settlement_is_idempotent():
    # Belt and braces: an explicit settle inside the block must not produce
    # a second row when `finally` runs.
    settled = _sink()
    with meter("call-3", PRICE, settled.append) as s:
        s.observe(Usage(prompt_tokens=1, completion_tokens=1))
        s.settle()
        s.settle()
    assert len(settled) == 1


def test_failure_before_any_token_still_settles_a_zero_row():
    # A zero row is not noise: "we tried and were charged nothing" and "we
    # never tried" are different facts, and only one of them means the
    # upstream call is missing from the invoice.
    settled = _sink()
    with pytest.raises(ValueError):
        with meter("call-4", PRICE, settled.append) as s:
            raise ValueError("upstream refused")
    assert len(settled) == 1
    assert settled[0].cost_nanousd == 0
    assert settled[0].status == "failed"


def test_abort_is_distinguished_from_upstream_failure():
    settled = _sink()
    with pytest.raises(RuntimeError):
        with meter("call-5", PRICE, settled.append) as s:
            s.observe(Usage(prompt_tokens=5, completion_tokens=0))
            s.add_completion_tokens(2)
            raise RuntimeError("client disconnected")
    assert settled[0].status == "aborted"   # tokens were produced
    settled.clear()
    with pytest.raises(RuntimeError):
        with meter("call-6", PRICE, settled.append) as s:
            raise RuntimeError("connect timeout")
    assert settled[0].status == "failed"    # nothing was produced


def test_session_rejects_observing_after_settlement():
    settled = _sink()
    s = MeterSession("call-7", PRICE, settled.append)
    s.observe(Usage(prompt_tokens=1, completion_tokens=1))
    s.settle()
    with pytest.raises(RuntimeError):
        s.observe(Usage(prompt_tokens=1, completion_tokens=1))

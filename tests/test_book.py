from datetime import datetime, timezone

import pytest

from nexus.ledger.book import (
    Entry,
    InMemoryLedger,
    UpstreamCharge,
    reconcile,
    rollup,
)
from nexus.ledger.usage import Usage

TS = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


def _entry(span, parent, cost, tokens=10, tenant="shopscout", call_id=None):
    return Entry(
        entry_id=f"e-{span}",
        call_id=call_id or f"c-{span}",
        tenant=tenant,
        workload="jury",
        trace_root="t-1",
        span_id=span,
        parent_span_id=parent,
        model="zai/glm-4.6",
        family="glm",
        usage=Usage(prompt_tokens=tokens, completion_tokens=0),
        cost_nanousd=cost,
        status="ok",
        ts=TS,
    )


def test_ledger_records_and_reads_back():
    book = InMemoryLedger()
    book.record(_entry("s1", None, 100))
    assert len(book.entries()) == 1


def test_rollup_totals_a_trace():
    book = InMemoryLedger()
    book.record(_entry("s1", "root", 100))
    book.record(_entry("s2", "root", 250))
    assert rollup(book.entries())["t-1"] == 350


def test_reconcile_is_clean_when_every_call_matches():
    entries = [_entry("s1", "root", 6000, tokens=10)]
    upstream = [UpstreamCharge(call_id="c-s1", model="zai/glm-4.6", cost_nanousd=6000)]
    assert reconcile(entries, upstream) == []


def test_reconcile_catches_an_amount_mismatch():
    entries = [_entry("s1", "root", 5999, tokens=10)]
    upstream = [UpstreamCharge(call_id="c-s1", model="zai/glm-4.6", cost_nanousd=6000)]
    problems = reconcile(entries, upstream)
    assert [p.kind for p in problems] == ["amount_mismatch"]


def test_reconcile_catches_a_missing_entry():
    # The aborted-stream bug in its observable form: upstream charged for a
    # call the ledger has no row for.
    upstream = [UpstreamCharge(call_id="c-s9", model="zai/glm-4.6", cost_nanousd=6000)]
    problems = reconcile([], upstream)
    assert [p.kind for p in problems] == ["missing_entry"]


def test_reconcile_catches_double_counting_of_a_parent_span():
    # Only leaf calls cost money. If an aggregating span is also billed, the
    # trace total is inflated while every individual row looks plausible --
    # and the sum still balances against itself, which is why this needs its
    # own check rather than falling out of the amount comparison.
    parent = _entry("s-parent", "root", 6000, call_id="c-parent")
    child = _entry("s-child", "s-parent", 6000, call_id="c-child")
    upstream = [
        UpstreamCharge(call_id="c-parent", model="zai/glm-4.6", cost_nanousd=6000),
        UpstreamCharge(call_id="c-child", model="zai/glm-4.6", cost_nanousd=6000),
    ]
    problems = reconcile([parent, child], upstream)
    assert "double_count" in [p.kind for p in problems]


def test_ledger_totals_never_use_floats():
    book = InMemoryLedger()
    for _ in range(100_000):
        book.record(_entry("s1", "root", 1))
    assert rollup(book.entries())["t-1"] == 100_000
    assert isinstance(rollup(book.entries())["t-1"], int)

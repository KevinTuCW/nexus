from dataclasses import replace
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


def test_entry_defaults_to_no_fallback():
    e = _entry("s1", None, 100)
    assert e.fallback_from is None


def test_entry_records_which_model_was_displaced():
    # Gate G4: a fallback that leaves no trace in the ledger is a silent
    # fallback, whatever the response body says.
    e = Entry(
        entry_id="e-1",
        call_id="c-1",
        tenant="wuwork",
        workload="default",
        trace_root=None,
        span_id="s-1",
        parent_span_id=None,
        model="zai/glm-4.7",
        family="glm",
        usage=Usage(prompt_tokens=1, completion_tokens=1),
        cost_nanousd=1,
        status="ok",
        ts=TS,
        fallback_from="zai/glm-4.6",
    )
    assert e.fallback_from == "zai/glm-4.6"


def test_aborted_rows_are_reconciled_as_a_lower_bound_not_an_equality():
    # Against a real provider, an aborted stream carries no usage frame:
    # nexus can only count the chunks it saw, and a chunk is not a token.
    # Asserting equality there would make G2 fail on correct behaviour;
    # asserting nothing would let real under-counting hide. The honest
    # middle is a lower bound.
    entry = replace(_entry("s1", None, 5000), status="aborted")
    upstream = [UpstreamCharge(call_id="c-s1", model="zai/glm-4.6", cost_nanousd=6000)]
    assert reconcile([entry], upstream) == []


def test_an_aborted_row_billing_more_than_the_upstream_is_still_a_problem():
    # A lower bound is still a bound. Over-billing an aborted call is not
    # excused by the approximation.
    entry = replace(_entry("s1", None, 7000), status="aborted")
    upstream = [UpstreamCharge(call_id="c-s1", model="zai/glm-4.6", cost_nanousd=6000)]
    assert [p.kind for p in reconcile([entry], upstream)] == ["amount_mismatch"]


def test_ok_rows_still_require_exact_equality():
    entry = _entry("s1", None, 5999)
    upstream = [UpstreamCharge(call_id="c-s1", model="zai/glm-4.6", cost_nanousd=6000)]
    assert [p.kind for p in reconcile([entry], upstream)] == ["amount_mismatch"]


def test_entry_records_the_full_model_chain():
    # requested -> routed -> served. Gates G1 and G4 each own one hop, and
    # neither can be judged from the ledger without both ends of its hop.
    e = replace(
        _entry("s1", None, 100),
        requested_model="zai/glm-4.6",
        routed_model="zai/glm-4.6",
    )
    assert e.requested_model == "zai/glm-4.6"
    assert e.routed_model == "zai/glm-4.6"


def test_older_rows_without_the_chain_still_load():
    # Rows written before Phase 3b have neither field. They must remain
    # readable: a schema change that makes history unreadable turns every
    # historical audit question into "we don't know".
    e = _entry("s1", None, 100)
    assert e.requested_model is None
    assert e.routed_model is None

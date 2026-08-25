import os
from datetime import datetime, timezone

import pytest

from nexus.ledger.usage import Usage

DSN = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DSN, reason="no DATABASE_URL in the shell; pg ledger skipped"
)


@pytest.fixture
def ledger():
    from nexus.ledger.pg import PgLedger

    book = PgLedger(DSN)
    book.execute("DELETE FROM ledger_entry")
    return book


def _entry(call_id, cost, status="ok", fallback_from=None):
    from nexus.ledger.book import Entry

    return Entry(
        entry_id=f"e-{call_id}",
        call_id=call_id,
        tenant="shopscout",
        workload="jury",
        trace_root=None,
        span_id=f"s-{call_id}",
        parent_span_id=None,
        model="zai/glm-4.6",
        family="glm",
        usage=Usage(prompt_tokens=10, completion_tokens=2),
        cost_nanousd=cost,
        status=status,
        ts=datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc),
        fallback_from=fallback_from,
    )


def test_round_trip_preserves_every_field(ledger):
    ledger.record(_entry("c-1", 6000, fallback_from="zai/glm-4.7"))
    (got,) = ledger.entries()
    assert got.call_id == "c-1"
    assert got.cost_nanousd == 6000
    assert got.usage == Usage(prompt_tokens=10, completion_tokens=2)
    assert got.fallback_from == "zai/glm-4.7"
    assert got.status == "ok"


def test_cost_survives_a_value_that_would_lose_precision_as_a_float(ledger):
    # 2**53 + 1 is the smallest integer a float64 cannot represent. If the
    # column or the driver ever went floating point, this comes back wrong
    # -- and every smaller amount would still look fine, which is why the
    # test uses this specific number rather than a merely large one.
    big = 2**53 + 1
    ledger.record(_entry("c-2", big))
    assert ledger.entries()[0].cost_nanousd == big


def test_the_same_call_cannot_be_billed_twice(ledger):
    import psycopg

    ledger.record(_entry("c-3", 6000))
    with pytest.raises(psycopg.errors.UniqueViolation):
        ledger.record(_entry("c-3", 6000))


def test_reconcile_works_over_persisted_rows(ledger):
    from nexus.ledger.book import UpstreamCharge, reconcile

    ledger.record(_entry("c-4", 6000))
    charges = [UpstreamCharge(call_id="c-4", model="zai/glm-4.6", cost_nanousd=6000)]
    assert reconcile(ledger.entries(), charges) == []


def test_aborted_status_round_trips_so_the_lower_bound_rule_still_applies(ledger):
    # The reconciliation rule for aborted rows keys off `status`. If that
    # column did not round-trip, every persisted row would be treated as an
    # exact measurement and legitimate aborted streams would fail the gate.
    from nexus.ledger.book import UpstreamCharge, reconcile

    ledger.record(_entry("c-5", 5000, status="aborted"))
    assert ledger.entries()[0].status == "aborted"
    charges = [UpstreamCharge(call_id="c-5", model="zai/glm-4.6", cost_nanousd=6000)]
    assert reconcile(ledger.entries(), charges) == []

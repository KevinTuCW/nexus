import os
from datetime import datetime, timezone

import pytest

from nexus.ledger.usage import Usage

DSN = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DSN, reason="no DATABASE_URL in the shell; pg ledger skipped"
)


#: These tests need an empty ledger to assert against, and they used to get
#: one with `DELETE FROM ledger_entry` on whatever DATABASE_URL named --
#: which `make test-live` reads out of `.env`. Point that at a ledger anyone
#: cares about and running the tests erases the artifact the platform exists
#: to produce, plus the evidence G1/G2/G4 are judged from. Nothing warns.
#:
#: So they get their own table, and it is created `LIKE ledger_entry
#: INCLUDING ALL` rather than from a second copy of the DDL: derived from the
#: real table, it cannot drift from it -- including the unique index on
#: call_id, which one of these tests is specifically about.
TEST_TABLE = "ledger_entry_pgtest"


@pytest.fixture
def ledger():
    from nexus.ledger.pg import PgLedger

    book = PgLedger(DSN, table=TEST_TABLE)
    book.execute(
        f"CREATE TABLE IF NOT EXISTS {TEST_TABLE} "
        "(LIKE ledger_entry INCLUDING ALL)"
    )
    book.execute(f"DELETE FROM {TEST_TABLE}")
    return book


def test_the_destructive_fixture_never_touches_the_real_ledger(ledger):
    # The guard on the guard. If someone "simplifies" the fixture back to the
    # real table, this notices -- and it notices by writing a row and then
    # looking for it in the place it must not be.
    import psycopg

    ledger.record(_entry("c-guard", 1))
    with psycopg.connect(DSN) as conn:
        leaked = conn.execute(
            "SELECT count(*) FROM ledger_entry WHERE call_id = %s", ("c-guard",)
        ).fetchone()[0]
    assert leaked == 0, "the pg tests are writing into the real ledger table"


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

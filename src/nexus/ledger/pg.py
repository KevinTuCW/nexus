"""Postgres-backed ledger.

Shares `Entry` with the in-memory implementation rather than defining a row
type of its own, so a field added to one cannot be forgotten in the other.
`db/schema.sql` is the third copy of that shape and the only one no type
checker will ever look at — `tests/test_pg_ledger.py` round-trips every
field precisely because a column missing there fails silently as a NULL.

`cost_nanousd` is BIGINT and comes back as an int. Money in this system is
integer nano-USD end to end; a driver or column that quietly went floating
point would be invisible for every small amount and wrong only for large
ones, which is the kind of defect that survives a long time.
"""

import psycopg

from nexus.ledger.book import Entry
from nexus.ledger.usage import Usage

_COLUMNS = (
    "entry_id, call_id, tenant, workload, trace_root, span_id, parent_span_id, "
    "model, family, prompt_tokens, completion_tokens, cache_write_tokens, "
    "cache_read_tokens, cost_nanousd, status, ts, fallback_from, "
    "requested_model, routed_model"
)


class PgLedger:
    """Same surface as `InMemoryLedger`: `record()` and `entries()`."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def execute(self, sql: str) -> None:
        with psycopg.connect(self._dsn) as conn:
            conn.execute(sql)

    def record(self, entry: Entry) -> None:
        with psycopg.connect(self._dsn) as conn:
            conn.execute(
                f"INSERT INTO ledger_entry ({_COLUMNS}) VALUES ("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    entry.entry_id,
                    entry.call_id,
                    entry.tenant,
                    entry.workload,
                    entry.trace_root,
                    entry.span_id,
                    entry.parent_span_id,
                    entry.model,
                    entry.family,
                    entry.usage.prompt_tokens,
                    entry.usage.completion_tokens,
                    entry.usage.cache_write_tokens,
                    entry.usage.cache_read_tokens,
                    entry.cost_nanousd,
                    entry.status,
                    entry.ts,
                    entry.fallback_from,
                    entry.requested_model,
                    entry.routed_model,
                ),
            )

    def entries(self) -> list[Entry]:
        with psycopg.connect(self._dsn) as conn:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM ledger_entry ORDER BY ts, entry_id"
            ).fetchall()
        return [
            Entry(
                entry_id=r[0],
                call_id=r[1],
                tenant=r[2],
                workload=r[3],
                trace_root=r[4],
                span_id=r[5],
                parent_span_id=r[6],
                model=r[7],
                family=r[8],
                usage=Usage(
                    prompt_tokens=r[9],
                    completion_tokens=r[10],
                    cache_write_tokens=r[11],
                    cache_read_tokens=r[12],
                ),
                cost_nanousd=r[13],
                status=r[14],
                ts=r[15],
                fallback_from=r[16],
                requested_model=r[17],
                routed_model=r[18],
            )
            for r in rows
        ]

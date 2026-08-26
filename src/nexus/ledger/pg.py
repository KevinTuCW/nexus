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

import re
from datetime import datetime

import psycopg

from nexus.ledger.book import Entry
from nexus.ledger.usage import Usage

_COLUMNS = (
    "entry_id, call_id, tenant, workload, trace_root, span_id, parent_span_id, "
    "model, family, prompt_tokens, completion_tokens, cache_write_tokens, "
    "cache_read_tokens, cost_nanousd, status, ts, fallback_from, "
    "requested_model, routed_model"
)


#: A table name is spliced into SQL, so it is checked rather than trusted.
#: Not because a caller is expected to be hostile -- because the moment an
#: identifier reaches an f-string, "nobody would pass that" is the only thing
#: standing between this module and an injection, and that is not a control.
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class PgLedger:
    """Same surface as `InMemoryLedger`: `record()` and `entries()`.

    `table` exists so the destructive tests have somewhere to be destructive.
    They need an empty ledger to assert against, and the way they got one was
    `DELETE FROM ledger_entry` against whatever `DATABASE_URL` happened to
    name -- which `make test-live` reads straight out of `.env`. Point that at
    a ledger anyone cares about and running the test suite erases the exact
    artifact this whole platform exists to produce, along with the evidence
    G1, G2 and G4 are audited from. Nothing warns; the tests pass.
    """

    def __init__(self, dsn: str, table: str = "ledger_entry") -> None:
        if not _IDENT.match(table):
            raise ValueError(f"not a bare SQL identifier: {table!r}")
        self._dsn = dsn
        self._table = table

    def execute(self, sql: str) -> None:
        with psycopg.connect(self._dsn) as conn:
            conn.execute(sql)

    def record(self, entry: Entry) -> None:
        with psycopg.connect(self._dsn) as conn:
            conn.execute(
                f"INSERT INTO {self._table} ({_COLUMNS}) VALUES ("
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
                f"SELECT {_COLUMNS} FROM {self._table} ORDER BY ts, entry_id"
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

    def spent_since(self, tenant: str, since: datetime) -> int:
        """Sum one tenant's spend in the database, not in this process.

        `COALESCE` because a tenant with no rows must come back as 0 rather
        than as NULL: `None > budget` raises, and a quota check that raises
        on a tenant's very first call of the day is a quota check that fails
        open or fails loud on precisely the wrong request.
        """
        with psycopg.connect(self._dsn) as conn:
            row = conn.execute(
                f"SELECT COALESCE(SUM(cost_nanousd), 0) FROM {self._table} "
                "WHERE tenant = %s AND ts >= %s",
                (tenant, since),
            ).fetchone()
        return int(row[0])

"""The cost ledger and its reconciliation — the core of gate G2.

Two rules carry the gate:

  1. **Only leaf calls cost money.** A span that has children is an
     aggregating view, not a charge. If an aggregate is also billed, the
     trace total inflates while every individual row still looks plausible
     and the ledger still balances against *itself*. Nothing short of a
     dedicated check finds it, which is why `double_count` is its own
     discrepancy kind rather than something the amount comparison would
     happen to catch.
  2. **Reconciliation compares against the upstream, not against ourselves.**
     A ledger checked only for internal consistency is a ledger that agrees
     with its own mistakes.

Amounts are integer nano-USD throughout; see `nexus.money`.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from nexus.ledger.usage import Usage


@dataclass(frozen=True)
class Entry:
    entry_id: str
    #: Identifies the upstream call this row bills, and is what
    #: reconciliation joins on.
    call_id: str
    tenant: str
    workload: str
    #: None for zero-touch tenants: their repos are unmodified, so they
    #: cannot propagate a trace root. Attribution for them is
    #: tenant/workload level, and G2's zero-error claim is made at that
    #: granularity — stated, not quietly assumed.
    trace_root: str | None
    span_id: str
    parent_span_id: str | None
    model: str
    family: str
    usage: Usage
    cost_nanousd: int
    status: Literal["ok", "aborted", "failed"]
    ts: datetime
    #: The model this call was originally routed to, when a fallback moved
    #: it elsewhere. Gate G4 requires that a fallback leave a trace in the
    #: ledger and not only in the response: a response is read once by one
    #: caller, while the ledger is what anyone asks afterwards.
    fallback_from: str | None = None
    #: What the tenant asked for, after alias resolution. Gate G1 compares
    #: this against `routed_model`: a substitution the policy never
    #: permitted is a violation regardless of what the router believed.
    requested_model: str | None = None
    #: What routing settled on, before any fallback. Gate G4 compares this
    #: against `model`: if the served model differs and `fallback_from`
    #: does not say so, the fallback was silent in the books.
    routed_model: str | None = None


@dataclass(frozen=True)
class UpstreamCharge:
    """What the provider says it charged for one call."""

    call_id: str
    model: str
    cost_nanousd: int


@dataclass(frozen=True)
class Discrepancy:
    kind: Literal["amount_mismatch", "missing_entry", "orphan_entry", "double_count"]
    detail: str


class Ledger(Protocol):
    """What the gateway needs from a ledger, memory-backed or persisted.

    Declared so `state.py` can hold either without importing psycopg, and so
    the Postgres implementation has a shape to satisfy rather than a class
    to subclass. Both implementations return the same `Entry` type: the row
    shape is defined once, so a field added to it cannot be quietly
    supported by one store and dropped by the other.

    `spent_since` is on the protocol rather than being computed from
    `entries()` because quota enforcement runs on the request path. Summing
    a full table per call would make every request pay for the whole history
    of the platform, and the cost would grow with adoption — the one metric
    an internal platform cannot afford to have working against it.
    `db/schema.sql` has carried a `(tenant, ts)` index since P1 for exactly
    this query; until now nothing asked it.
    """

    def record(self, entry: "Entry") -> None: ...

    def entries(self) -> list["Entry"]: ...

    def spent_since(self, tenant: str, since: datetime) -> int: ...


class InMemoryLedger:
    """P1's ledger. Postgres wiring lands in P2; `db/schema.sql` already
    describes the target table so the two cannot drift apart silently."""

    def __init__(self) -> None:
        self._entries: list[Entry] = []

    def record(self, entry: Entry) -> None:
        self._entries.append(entry)

    def entries(self) -> list[Entry]:
        return list(self._entries)

    def spent_since(self, tenant: str, since: datetime) -> int:
        return sum(
            e.cost_nanousd
            for e in self._entries
            if e.tenant == tenant and e.ts >= since
        )


def rollup(entries: list[Entry]) -> dict[str, int]:
    """Total nano-USD per trace root. Integer arithmetic only."""
    totals: dict[str, int] = {}
    for e in entries:
        key = e.trace_root or f"tenant:{e.tenant}/{e.workload}"
        totals[key] = totals.get(key, 0) + e.cost_nanousd
    return totals


def reconcile(entries: list[Entry], upstream: list[UpstreamCharge]) -> list[Discrepancy]:
    """Compare the ledger against what providers actually charged.

    Returns every problem found. Gate G2 passes only on an empty list.
    """
    problems: list[Discrepancy] = []

    parents = {e.parent_span_id for e in entries if e.parent_span_id}
    for e in entries:
        if e.span_id in parents:
            problems.append(
                Discrepancy(
                    kind="double_count",
                    detail=(
                        f"span {e.span_id} has children and is also billed "
                        f"{e.cost_nanousd} nano-USD; only leaf calls cost money"
                    ),
                )
            )

    by_call = {e.call_id: e for e in entries}
    for charge in upstream:
        entry = by_call.pop(charge.call_id, None)
        if entry is None:
            problems.append(
                Discrepancy(
                    kind="missing_entry",
                    detail=(
                        f"upstream charged {charge.cost_nanousd} nano-USD for call "
                        f"{charge.call_id} ({charge.model}) but the ledger has no row"
                    ),
                )
            )
            continue
        # `aborted` rows are a lower bound, not a measurement. A real
        # provider sends no usage frame when the client hangs up mid-stream,
        # so nexus can only count the chunks it saw -- and a chunk is not a
        # token. Demanding equality there would fail the gate on correct
        # behaviour; demanding nothing would let genuine under-counting
        # hide. Over-billing an aborted call is still a problem: a bound is
        # a bound, and "we approximated" is not a licence to approximate
        # upwards.
        exact = entry.status != "aborted"
        if (exact and entry.cost_nanousd != charge.cost_nanousd) or (
            not exact and entry.cost_nanousd > charge.cost_nanousd
        ):
            problems.append(
                Discrepancy(
                    kind="amount_mismatch",
                    detail=(
                        f"call {charge.call_id}: ledger {entry.cost_nanousd} "
                        f"{'!=' if exact else '>'} upstream "
                        f"{charge.cost_nanousd} nano-USD"
                        + ("" if exact else " (aborted rows are a lower bound)")
                    ),
                )
            )

    for leftover in by_call.values():
        problems.append(
            Discrepancy(
                kind="orphan_entry",
                detail=(
                    f"ledger bills call {leftover.call_id} but the upstream "
                    "reported no such charge"
                ),
            )
        )

    return problems

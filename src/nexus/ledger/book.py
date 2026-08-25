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
from typing import Literal

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


class InMemoryLedger:
    """P1's ledger. Postgres wiring lands in P2; `db/schema.sql` already
    describes the target table so the two cannot drift apart silently."""

    def __init__(self) -> None:
        self._entries: list[Entry] = []

    def record(self, entry: Entry) -> None:
        self._entries.append(entry)

    def entries(self) -> list[Entry]:
        return list(self._entries)


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
        if entry.cost_nanousd != charge.cost_nanousd:
            problems.append(
                Discrepancy(
                    kind="amount_mismatch",
                    detail=(
                        f"call {charge.call_id}: ledger {entry.cost_nanousd} != "
                        f"upstream {charge.cost_nanousd} nano-USD"
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

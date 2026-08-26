"""Per-tenant daily budget enforcement.

Budget 0 means "switched off", not "unlimited". Reading a missing or zero
budget as unmetered is a classic off-by-semantics bug, and its failure mode
is that the least-configured tenant gets the most spend.

Not a hard gate for this project — the hard gates are G1–G4, and a budget is
a business control rather than a correctness claim. It is still *enforced*,
because the alternative is worse than not having it: a console that renders
a budget beside a spend it never acts on teaches everyone who reads it that
the number means something, and the day it is exceeded nothing happens.
For a whole quarter that reads exactly like a working control.

**The incoming call is charged at its floor, not at an estimate.** What a
call will cost is not knowable before it returns — it depends on how many
tokens come back — and inventing a figure here would put a made-up number in
the one place the platform promises not to: the decision about whether to
serve. So the check asserts only what is certain, that a served call costs at
least `MIN_CALL_NANOUSD`. The consequence is stated rather than hidden: a
tenant can overshoot its budget by at most the cost of the single call that
crossed the line, and concurrent calls in flight when the line is crossed are
all served. A per-call reservation scheme would close that window and would
need an estimate to reserve against, which is the trade this file refuses.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

#: The floor cost of serving one call: not an estimate of it. Its only job is
#: to make `spent >= budget` refuse without special-casing equality, and to
#: keep a zero budget switched off rather than trivially satisfiable.
MIN_CALL_NANOUSD = 1


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str


def day_start(now: datetime) -> datetime:
    """The UTC midnight that opens `now`'s budget day.

    UTC, not local time, and stated here rather than assumed at each call
    site: a budget day that moves with the server's timezone gives tenants
    two partial days whenever a host is reprovisioned in another region,
    and the resulting refusals are unreproducible.
    """
    return now.astimezone(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def next_day_start(now: datetime) -> datetime:
    """When the refused tenant gets its budget back. Fed to `Retry-After`."""
    return day_start(now) + timedelta(days=1)


def check_budget(spent_today: int, incoming_estimate: int, budget: int) -> Decision:
    """All amounts in integer nano-USD."""
    projected = spent_today + incoming_estimate
    if projected > budget:
        return Decision(
            allowed=False,
            reason=(
                f"daily budget exceeded: spent {spent_today} + estimated "
                f"{incoming_estimate} = {projected} > budget {budget} nano-USD"
            ),
        )
    return Decision(allowed=True, reason="within budget")

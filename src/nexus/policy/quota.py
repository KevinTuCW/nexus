"""Per-tenant daily budget enforcement.

Budget 0 means "switched off", not "unlimited". Reading a missing or zero
budget as unmetered is a classic off-by-semantics bug, and its failure mode
is that the least-configured tenant gets the most spend.

Not a hard gate for this project — enforcement here is a feature with soft
metrics. The hard gates are G1–G4.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str


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

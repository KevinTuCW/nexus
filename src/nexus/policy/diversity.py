"""Gate G1: routing optimisation must not collapse declared model diversity.

Two tenants' correctness rests on models being *different*: shopscout runs a
three-lab jury, and a sibling project not integrated here reads every image
twice with deliberately orthogonal models. Their guarantee used to be
physical — their own configs pointed at two different providers. Routing
everything through one gateway converted that physical fact into a promise
this module has to keep.

The dangerous part is that breaking it looks like success. A router that
serves the GLM juror from the cheapest Qwen3 saves real money on day one;
the jury silently becomes two Qwen3s and a DeepSeek; and no response field,
ledger row or dashboard says anything. The bill improves. The cross-check is
dead.

Two enforcement modes, because the two integration styles carry different
information — and **no heuristic bridges them**. Guessing which calls belong
to one jury from timing would produce a gate that is right most of the time,
and a gate that is right most of the time is not a gate:

  - **zero-touch tenants** — nexus infers no grouping at all. `guard()`
    checks each substitution against the per-model permission the tenant
    declared in `policies/<tenant>.yaml`. Pinned models simply never move,
    so a jury stays diverse without nexus ever knowing it was a jury.
  - **native tenants** — the caller passes a group id, and `GroupLedger`
    reserves one weight family per member, failing loudly when the families
    run out.
"""

from nexus.policy.routing import RouteDecision
from nexus.registry.families import UNKNOWN_FAMILY, family_of
from nexus.registry.tenants import TenantPolicy, substitution_allowed


class DiversityViolation(Exception):
    """A routing decision would have replaced a model the tenant pinned."""


class DiversityExhausted(Exception):
    """No candidate is left whose weight family is unused in this group."""


def guard(policy: TenantPolicy, decision: RouteDecision) -> None:
    """Veto a substitution the tenant never permitted.

    Compares the models themselves rather than reading
    `decision.substituted`, and re-derives the permission from the policy
    rather than reading `decision.reason`. Both fields are the router's
    account of its own behaviour, and the router is the component most
    likely to be rewritten in pursuit of cost — a rewrite that swaps the
    model while leaving `substituted=False` would otherwise walk straight
    past this function. A guard that believes the thing it is guarding
    guards nothing.
    """
    if decision.model == decision.requested:
        return
    target = family_of(decision.model)
    if not substitution_allowed(policy, decision.requested, target):
        raise DiversityViolation(
            f"tenant '{policy.tenant}' pinned '{decision.requested}'; routing "
            f"tried to serve it from '{decision.model}' (family '{target}'). "
            "Refusing: this is how a cross-lab jury becomes an echo while the "
            "bill improves."
        )


class GroupLedger:
    """Per-group family reservations for native tenants."""

    def __init__(self) -> None:
        self._used: dict[str, set[str]] = {}

    def reserve(self, group_id: str, candidates: list[str]) -> str:
        """Take the first candidate whose family is unused in this group.

        Raises rather than returning a duplicate. Silently repeating a
        family is the failure this class exists to prevent, and it is
        invisible downstream — same as aura's rule that a guarantee which
        cannot be met must fail loudly instead of degrading quietly.
        """
        used = self._used.setdefault(group_id, set())
        for model in candidates:
            family = family_of(model)
            if family == UNKNOWN_FAMILY:
                # An unregistered model cannot be counted towards diversity:
                # accepting it would let a stale registry satisfy the
                # requirement by accident.
                continue
            if family not in used:
                used.add(family)
                return model
        raise DiversityExhausted(
            f"group '{group_id}' has already used families {sorted(used)}; "
            f"none of {candidates} contributes a new known family"
        )

    def release(self, group_id: str) -> None:
        self._used.pop(group_id, None)

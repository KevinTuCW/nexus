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

from collections import OrderedDict

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
    """Per-group family reservations for native tenants.

    **A family is spent when a call succeeds, not when one is planned.** The
    first version reserved before the provider was called and never looked
    again, which produced two failures in opposite directions:

      - the reserved model failed, the fallback chain served something else,
        and nothing re-checked the family. A group could end up with two
        members on one weight family while this ledger recorded that it had
        not — the exact collapse G1 exists to catch, arriving through the
        one path that was allowed to skip it;
      - a family was consumed by a call that never happened, so a group could
        run out of families on models it had never used and fail a juror
        that had every right to be served.

    Hence `candidates()` (what is still available) and `commit()` (what was
    actually served). Planning is free; only delivery costs a family.

    Bounded, for the same reason `RoutingLog` is. `group_id` arrives from the
    caller, `release()` was never invoked by anyone, and an unbounded dict
    keyed by a caller-supplied string is a leak that reads like a feature
    until the process is old enough to matter. Eviction is least-recently-
    used: a group still being filled in must outlive unrelated traffic, or
    the bound would have traded a memory leak for a silent diversity failure.
    """

    #: Enough for the deepest jury any tenant runs, times a wide margin for
    #: groups still in flight. Sized rather than unbounded — see class doc.
    DEFAULT_CAPACITY = 1024

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        self._capacity = capacity
        self._used: OrderedDict[str, set[str]] = OrderedDict()

    def _touch(self, group_id: str) -> set[str]:
        if group_id in self._used:
            self._used.move_to_end(group_id)
        else:
            self._used[group_id] = set()
            while len(self._used) > self._capacity:
                self._used.popitem(last=False)
        return self._used[group_id]

    def candidates(self, group_id: str, candidates: list[str]) -> list[str]:
        """The subset of `candidates` whose families this group has not used.

        Order is preserved: the caller's preference (cheapest first, from the
        fallback chain) still decides, this only removes what diversity
        forbids. Unregistered models are dropped rather than allowed through
        — counting an unknown family towards diversity would let a stale
        registry satisfy the requirement by accident.

        Candidates sharing a family with *each other* are all kept. The
        diversity rule is about group members, not about one member's retry
        chain: a juror that falls back from `glm-4.6` to `glm-4.7` still
        occupies exactly one seat and one family. De-duplicating here instead
        would delete a tenant's entire fallback chain — `substitutable_to:
        [glm]` permits precisely the same-family alternatives — and turn a
        single provider blip into a 503 for a request that had somewhere to
        go.
        """
        used = self._touch(group_id)
        return [
            model
            for model in candidates
            if family_of(model) != UNKNOWN_FAMILY and family_of(model) not in used
        ]

    def commit(self, group_id: str, model: str) -> None:
        """Record that `model` was served to this group. Idempotent."""
        family = family_of(model)
        if family == UNKNOWN_FAMILY:
            raise DiversityExhausted(
                f"group '{group_id}' was served '{model}', which is not in the "
                "weight-family registry; it cannot be counted towards "
                "diversity and must not be silently accepted"
            )
        self._touch(group_id).add(family)

    def reserve(self, group_id: str, candidates: list[str]) -> str:
        """Take the first candidate whose family is unused in this group.

        Plan-and-commit in one step. Kept for callers that genuinely cannot
        fail between the two — and raising rather than returning a duplicate,
        because silently repeating a family is the failure this class exists
        to prevent and it is invisible downstream.
        """
        available = self.candidates(group_id, candidates)
        if not available:
            raise DiversityExhausted(
                f"group '{group_id}' has already used families "
                f"{sorted(self._used.get(group_id, set()))}; none of "
                f"{candidates} contributes a new known family"
            )
        self.commit(group_id, available[0])
        return available[0]

    def tracked_groups(self) -> int:
        """How many groups are currently held. Exposed so the bound is testable."""
        return len(self._used)

    def release(self, group_id: str) -> None:
        self._used.pop(group_id, None)

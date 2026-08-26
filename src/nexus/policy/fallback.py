"""Gate G4: a fallback is never silent, and never overrides a pin.

Two rules, both about what failure is allowed to change.

**A tenant may forbid fallback entirely.** wealthwise does. On a compliance
path, quietly answering from a weaker model is worse than returning nothing:
the answer still looks like an answer, and the caller has no way to know the
reasoning behind it got cheaper.

**Failure does not suspend the diversity rule.** The chain is built from the
same permissions routing uses, so a pinned model has nowhere to fall back
to. The tempting alternative — "well, it's an outage, anything is better
than an error" — would make the pin hold on every day except the day it
matters.
"""

from nexus.money import Price
from nexus.policy.routing import RouteDecision, rank_key
from nexus.registry.families import family_of
from nexus.registry.tenants import TenantPolicy, substitution_allowed


def fallback_chain(
    policy: TenantPolicy, decision: RouteDecision, prices: dict[str, Price]
) -> tuple[str, ...]:
    """Ordered alternatives to try if the served model fails.

    Empty when the tenant forbids fallback, and empty when the requested
    model is pinned. Cheapest first, on the same proxy routing uses.

    **The tenant's own requested model is always in the chain.** It needs no
    permission: serving what was asked for is not a substitution, and
    `guard()` returns early on it. Deriving the chain purely from
    `substitution_allowed` left it out whenever a model's own family was
    absent from its `substitutable_to` list -- so a tenant whose cheaper
    substitute was down got a 503 while the model it actually asked for sat
    there healthy. Routing is allowed to look for something cheaper; it is
    not allowed to make the tenant's own choice unreachable.
    """
    if not policy.allow_fallback:
        return ()
    alternatives = [
        model
        for model in prices
        if model != decision.model
        and (
            model == decision.requested
            or substitution_allowed(policy, decision.requested, family_of(model))
        )
    ]
    return tuple(sorted(alternatives, key=lambda m: rank_key(prices[m])))

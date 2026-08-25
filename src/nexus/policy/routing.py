"""Candidate selection: which model will actually be called.

The router only knows how to be cheap. It does not know, and must not be
trusted to know, whether being cheap is allowed — that judgement lives in
`nexus.policy.diversity`, which can veto any decision made here. Keeping the
two apart is what makes gate G1's falsification meaningful: replacing this
module with a greedy cheapest-first pick must still be caught, and it can
only be caught by something that is not this module.

Ranking uses a *proxy*, not a real cost. The true cost of a call is not
knowable before the call: it depends on how many tokens come back. The proxy
weights the prompt rate more heavily than the completion rate because the
tenants here send long contexts and take short answers — a retrieval answer,
a jury verdict, a classification. A workload with the opposite shape would
want a different weighting, which is why the weight is named and documented
rather than inlined.
"""

from dataclasses import dataclass

from nexus.money import Price
from nexus.registry.families import family_of
from nexus.registry.tenants import TenantPolicy, substitution_allowed

#: Nominal prompt:completion ratio used to order candidates. See the module
#: docstring — this is an ordering heuristic, never a billed amount.
PROMPT_WEIGHT = 3


@dataclass(frozen=True)
class RouteDecision:
    requested: str
    model: str
    substituted: bool
    reason: str


def rank_key(price: Price) -> int:
    """Ordering proxy for 'how expensive is this model'."""
    return price.prompt * PROMPT_WEIGHT + price.completion


def choose(
    policy: TenantPolicy, requested: str, prices: dict[str, Price]
) -> RouteDecision:
    """Pick the model to call for a request.

    Candidates are the requested model plus every priced model whose weight
    family the tenant has explicitly permitted for it. Unpriced models are
    excluded: serving one would write a zero-cost ledger row that reconciles
    perfectly against a zero-cost charge.
    """
    if requested not in prices:
        return RouteDecision(
            requested=requested,
            model=requested,
            substituted=False,
            reason="no price on file; left untouched for the caller to refuse",
        )

    alternatives = [
        model
        for model in prices
        if model != requested
        and substitution_allowed(policy, requested, family_of(model))
    ]
    if not alternatives:
        return RouteDecision(
            requested=requested,
            model=requested,
            substituted=False,
            reason="no substitution permitted by policy",
        )

    best = min(alternatives, key=lambda m: rank_key(prices[m]))
    # Ties prefer the requested model. Reshuffling equal-cost models churns
    # the ledger and the traces for no gain, and makes day-over-day
    # comparisons meaningless.
    if rank_key(prices[best]) >= rank_key(prices[requested]):
        return RouteDecision(
            requested=requested,
            model=requested,
            substituted=False,
            reason="requested model is already the cheapest permitted option",
        )
    return RouteDecision(
        requested=requested,
        model=best,
        substituted=True,
        reason=f"cheaper permitted alternative in family '{family_of(best)}'",
    )

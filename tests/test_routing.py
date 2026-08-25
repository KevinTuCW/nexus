import pytest

from nexus.policy.routing import RouteDecision, choose, rank_key
from nexus.registry.tenants import load_policies
from nexus.upstream import PRICES


@pytest.fixture
def policies(policies_dir):
    return load_policies(policies_dir)


def test_pinned_model_is_served_as_requested(policies):
    # shopscout's jury members carry no substitutable_to, so they are
    # pinned and the router has nothing to consider.
    d = choose(policies["shopscout"], "zai/glm-4.6", PRICES)
    assert d.model == "zai/glm-4.6"
    assert d.substituted is False


def test_router_may_swap_within_a_permitted_family(policies):
    # helpmate's router model may be served by any qwen3. Qwen3-8B is
    # already the cheapest qwen3, so the decision is stable...
    d = choose(policies["helpmate"], "siliconflow/Qwen/Qwen3-8B", PRICES)
    assert d.model == "siliconflow/Qwen/Qwen3-8B"
    assert d.substituted is False


def test_router_picks_the_cheaper_permitted_alternative(policies, monkeypatch):
    # ...and when the requested one is not the cheapest of its permitted
    # set, the router moves. wuwork permits glm -> glm, and glm-4.6/4.7 are
    # priced identically, so build the case explicitly rather than relying
    # on the shipped table happening to have a gap.
    #
    # The stand-in must be registered in FAMILIES too: an unregistered model
    # has family UNKNOWN, which no policy permits, so it would never become
    # a candidate and this test would pass for the wrong reason.
    from nexus.money import Price
    from nexus.registry.families import FAMILIES, FamilyRecord

    monkeypatch.setitem(
        FAMILIES, "zai/glm-cheap", FamilyRecord(family="glm", basis="test double")
    )
    prices = dict(PRICES)
    prices["zai/glm-cheap"] = Price(prompt=1, completion=1)
    d = choose(policies["wuwork"], "zai/glm-4.6", prices)
    assert d.model == "zai/glm-cheap"
    assert d.substituted is True
    assert "cheaper" in d.reason


def test_unlisted_model_is_never_substituted(policies):
    # Default-deny, enforced here as well as in the policy loader: a model
    # nobody configured must come out exactly as it went in.
    d = choose(policies["shopscout"], "siliconflow/deepseek-ai/DeepSeek-V3", PRICES)
    assert d.model == "siliconflow/deepseek-ai/DeepSeek-V3"
    assert d.substituted is False


def test_candidates_without_a_price_are_not_considered(policies):
    # Serving an unpriced model would write a zero-cost ledger row. The
    # router must not reach for one even when the family matches.
    prices = dict(PRICES)
    prices.pop("dashscope/qwen3-235b-a22b")
    d = choose(policies["helpmate"], "siliconflow/Qwen/Qwen3-8B", prices)
    assert d.model in prices


def test_ties_prefer_the_requested_model(policies):
    # glm-4.6 and glm-4.7 are priced identically. A router that reshuffles
    # equal-cost models produces churn in the ledger and in traces for no
    # gain, and makes A/B comparisons across days meaningless.
    d = choose(policies["wuwork"], "zai/glm-4.6", PRICES)
    assert d.model == "zai/glm-4.6"
    assert d.substituted is False


def test_rank_key_weights_prompt_more_than_completion():
    from nexus.money import Price

    cheap_prompt = Price(prompt=100, completion=1000)
    cheap_completion = Price(prompt=1000, completion=100)
    assert rank_key(cheap_prompt) < rank_key(cheap_completion)


def test_decision_is_frozen(policies):
    d = choose(policies["shopscout"], "zai/glm-4.6", PRICES)
    assert isinstance(d, RouteDecision)
    with pytest.raises(Exception):
        d.model = "x"  # type: ignore[misc]

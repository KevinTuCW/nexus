import pytest

from nexus.policy.fallback import fallback_chain
from nexus.policy.routing import choose
from nexus.registry.tenants import load_policies
from nexus.upstream import PRICES


@pytest.fixture
def policies(policies_dir):
    return load_policies(policies_dir)


def test_wealthwise_gets_no_chain_at_all(policies):
    # allow_fallback: false. In a compliance path a silent downgrade to a
    # weaker model is worse than a loud 503 -- the answer still looks like
    # an answer.
    d = choose(policies["wealthwise"], "zai/glm-4.6", PRICES)
    assert fallback_chain(policies["wealthwise"], d, PRICES) == ()


def test_a_permissive_tenant_gets_permitted_alternatives(policies):
    d = choose(policies["wuwork"], "zai/glm-4.6", PRICES)
    chain = fallback_chain(policies["wuwork"], d, PRICES)
    assert "zai/glm-4.7" in chain


def test_the_chain_never_contains_the_served_model(policies):
    d = choose(policies["wuwork"], "zai/glm-4.6", PRICES)
    assert d.model not in fallback_chain(policies["wuwork"], d, PRICES)


def test_fallback_obeys_the_same_diversity_rule_as_routing(policies):
    # A pinned model has nowhere to fall back to. Letting failure be the
    # one condition under which pinning stops applying would make the
    # guarantee hold exactly until the day it is needed.
    d = choose(policies["shopscout"], "zai/glm-4.6", PRICES)
    assert fallback_chain(policies["shopscout"], d, PRICES) == ()


def test_chain_is_ordered_cheapest_first(policies, monkeypatch):
    # Both stand-ins must be registered in FAMILIES: an unregistered model
    # has family UNKNOWN, which no policy permits, so it would never reach
    # the chain and the ordering assertion would hold vacuously.
    from nexus.money import Price
    from nexus.policy.routing import rank_key
    from nexus.registry.families import FAMILIES, FamilyRecord

    for name in ("zai/glm-mid", "zai/glm-dear"):
        monkeypatch.setitem(
            FAMILIES, name, FamilyRecord(family="glm", basis="test double")
        )
    prices = dict(PRICES)
    prices["zai/glm-mid"] = Price(prompt=500, completion=2000)
    prices["zai/glm-dear"] = Price(prompt=900, completion=3000)
    d = choose(policies["aura"], "zai/glm-4.6", prices)
    assert len(chain := fallback_chain(policies["aura"], d, prices)) >= 2, (
        f"chain {chain} is too short to say anything about ordering"
    )
    keys = [rank_key(prices[m]) for m in chain]
    assert keys == sorted(keys)


def test_unpriced_models_never_enter_the_chain(policies):
    d = choose(policies["wuwork"], "zai/glm-4.6", PRICES)
    for model in fallback_chain(policies["wuwork"], d, PRICES):
        assert model in PRICES

from dataclasses import replace

import pytest

from nexus.policy.fallback import fallback_chain
from nexus.policy.routing import choose
from nexus.registry.tenants import ModelPolicy, TenantPolicy, load_policies
from nexus.upstream import PRICES


@pytest.fixture
def policies(policies_dir):
    return load_policies(policies_dir)


def test_wealthwise_gets_no_chain_at_all(policies):
    # allow_fallback: false. In a compliance path a silent downgrade to a
    # weaker model is worse than a loud 503 -- the answer still looks like
    # an answer.
    #
    # NOTE: this asserts the behaviour but does not isolate its cause --
    # wealthwise also pins both its models, so the chain would be empty even
    # with the allow_fallback rule deleted. See the next test, which is the
    # one that actually holds that rule up.
    d = choose(policies["wealthwise"], "zai/glm-4.6", PRICES)
    assert fallback_chain(policies["wealthwise"], d, PRICES) == ()


def test_forbidding_fallback_is_enforced_on_a_substitutable_model():
    # The real tenant that forbids fallback happens to pin every model it
    # uses, so the obvious test above passes for a reason unrelated to its
    # name: deleting the allow_fallback check entirely leaves it green. That
    # was found by deliberately breaking the implementation and watching
    # nothing go red.
    #
    # This case cannot be built from policies/*.yaml, because no shipped
    # tenant combines the two properties. Constructed here instead: fallback
    # forbidden AND a substitutable model, so an empty chain can only come
    # from the allow_fallback rule.
    forbidding = TenantPolicy(
        tenant="forbids-fallback",
        integration="native",
        repo_path=None,
        gate_command="make test",
        api_key_env="NEXUS_KEY_UNUSED",
        allow_fallback=False,
        budget_nanousd_per_day=1,
        models={"zai/glm-4.6": ModelPolicy(substitutable_to=("glm",))},
    )
    assert fallback_chain(forbidding, choose(forbidding, "zai/glm-4.6", PRICES), PRICES) == ()

    # Control. Flip only allow_fallback and a chain must appear -- otherwise
    # the assertion above would be satisfied by some third reason and we
    # would be back where we started.
    allowing = replace(forbidding, allow_fallback=True)
    assert fallback_chain(allowing, choose(allowing, "zai/glm-4.6", PRICES), PRICES) != ()


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


def test_the_requested_model_is_always_reachable_as_a_fallback():
    """Routing may look for something cheaper. It may not make the tenant's
    own choice unreachable.

    Built rather than loaded, because no shipped policy has the shape: a
    model permitted to move to *another* family but not listed as permitting
    its own. Under the old chain, routing would substitute to the cheaper
    qwen3, and if that qwen3 was down the tenant got a 503 while the glm it
    actually asked for was healthy and idle.
    """
    policy = TenantPolicy(
        tenant="cross-family",
        integration="native",
        repo_path=None,
        gate_command="make test",
        api_key_env="NEXUS_KEY_UNUSED",
        allow_fallback=True,
        budget_nanousd_per_day=1,
        models={"zai/glm-4.6": ModelPolicy(substitutable_to=("qwen3",))},
    )
    d = choose(policy, "zai/glm-4.6", PRICES)
    assert d.model != "zai/glm-4.6", "routing was expected to substitute here"
    assert "zai/glm-4.6" in fallback_chain(policy, d, PRICES)


def test_a_pinned_model_still_has_an_empty_chain(policies):
    # The control. "Always include the requested model" must not become a
    # back door that hands a pinned juror somewhere to go: the requested
    # model is the served model here, and the chain excludes it as before.
    d = choose(policies["shopscout"], "zai/glm-4.6", PRICES)
    assert fallback_chain(policies["shopscout"], d, PRICES) == ()

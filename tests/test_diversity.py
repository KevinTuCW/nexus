import pytest

from nexus.policy.diversity import (
    DiversityExhausted,
    DiversityViolation,
    GroupLedger,
    guard,
)
from nexus.policy.routing import RouteDecision, choose
from nexus.registry.tenants import load_policies
from nexus.upstream import PRICES


@pytest.fixture
def policies(policies_dir):
    return load_policies(policies_dir)


def _forced(requested, model):
    """A decision the router would not have made, as a greedy router would."""
    return RouteDecision(
        requested=requested, model=model, substituted=True, reason="greedy"
    )


def test_a_permitted_substitution_passes(policies):
    d = choose(policies["helpmate"], "siliconflow/Qwen/Qwen3-8B", PRICES)
    guard(policies["helpmate"], d)  # must not raise


def test_unsubstituted_decisions_always_pass(policies):
    d = choose(policies["shopscout"], "zai/glm-4.6", PRICES)
    guard(policies["shopscout"], d)  # must not raise


def test_swapping_a_pinned_jury_member_is_vetoed(policies):
    # The failure gate G1 exists for. A greedy router would serve
    # shopscout's GLM juror from the cheapest qwen3 and save real money;
    # the jury would then be two Qwen3s and a DeepSeek, and nothing in the
    # response, the ledger or the dashboards would say so.
    with pytest.raises(DiversityViolation) as exc:
        guard(
            policies["shopscout"],
            _forced("zai/glm-4.6", "siliconflow/Qwen/Qwen3-8B"),
        )
    assert "zai/glm-4.6" in str(exc.value)


def test_a_router_that_lies_about_substituting_is_still_caught(policies):
    # The guard must not read `decision.substituted`. A router rewritten for
    # cost could swap the model and leave that flag False -- honestly, by
    # mistake, or because someone "simplified" the decision object. Trusting
    # it would make the guard a formality that the one component it exists
    # to police gets to switch off.
    with pytest.raises(DiversityViolation):
        guard(
            policies["shopscout"],
            RouteDecision(
                requested="zai/glm-4.6",
                model="siliconflow/Qwen/Qwen3-8B",
                substituted=False,
                reason="honest, I promise",
            ),
        )


def test_veto_names_the_family_it_protected(policies):
    with pytest.raises(DiversityViolation) as exc:
        guard(
            policies["wealthwise"],
            _forced("zai/glm-4.6", "siliconflow/Qwen/Qwen3-235B-A22B"),
        )
    assert "qwen3" in str(exc.value)


def test_group_reserves_one_family_per_member():
    g = GroupLedger()
    first = g.reserve("jury-1", ["zai/glm-4.6", "siliconflow/Qwen/Qwen3-8B"])
    second = g.reserve("jury-1", ["zai/glm-4.7", "siliconflow/Qwen/Qwen3-8B"])
    assert first == "zai/glm-4.6"
    # glm is taken, so the second member must come from another family.
    assert second == "siliconflow/Qwen/Qwen3-8B"


def test_group_exhaustion_fails_loudly_rather_than_repeating_a_family():
    # Aligned with aura's rule: when the guarantee cannot be met, fail
    # loudly. Silently serving a duplicate family is what turns a jury into
    # an echo.
    g = GroupLedger()
    g.reserve("jury-2", ["zai/glm-4.6"])
    with pytest.raises(DiversityExhausted):
        g.reserve("jury-2", ["zai/glm-4.7"])


def test_groups_are_independent():
    g = GroupLedger()
    g.reserve("jury-a", ["zai/glm-4.6"])
    assert g.reserve("jury-b", ["zai/glm-4.7"]) == "zai/glm-4.7"


def test_releasing_a_group_frees_its_families():
    g = GroupLedger()
    g.reserve("jury-3", ["zai/glm-4.6"])
    g.release("jury-3")
    assert g.reserve("jury-3", ["zai/glm-4.7"]) == "zai/glm-4.7"


def test_unknown_family_candidates_are_refused_in_groups():
    # An unregistered model has family UNKNOWN. Reserving it would let an
    # unmaintained registry satisfy a diversity requirement by accident.
    g = GroupLedger()
    with pytest.raises(DiversityExhausted):
        g.reserve("jury-4", ["some-vendor/not-in-the-registry"])

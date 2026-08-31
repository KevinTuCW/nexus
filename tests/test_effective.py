import pytest

from nexus.registry.effective import (
    Override,
    compose,
    load_effective_policies,
)
from nexus.registry.tenants import load_policies

# --- construction ---


def test_an_override_cannot_name_a_field_outside_the_capability_set():
    # Budget is not a capability. Letting it in here would require a
    # `new_value` field, and once that field exists "can only subtract"
    # stops being a structural fact and becomes a comment.
    with pytest.raises(ValueError, match="budget"):
        Override(tenant="wuwork", field="budget_nanousd_per_day", removed_value="1")


def test_a_substitutable_to_override_must_say_which_model():
    with pytest.raises(ValueError, match="model"):
        Override(tenant="wuwork", field="substitutable_to", removed_value="qwen3")


# --- narrowing, field by field ---


def test_compose_removes_one_substitutable_family(policies_dir):
    declared = load_policies(policies_dir)["wuwork"]
    model = "siliconflow/Qwen/Qwen3-8B"
    assert "qwen3" in declared.models[model].substitutable_to

    effective = compose(
        declared,
        [
            Override(
                tenant="wuwork",
                field="substitutable_to",
                removed_value="qwen3",
                model=model,
            )
        ],
    )

    assert effective.models[model].substitutable_to == ()
    # The declared policy is not mutated in place.
    assert "qwen3" in declared.models[model].substitutable_to


def test_compose_removes_one_cross_tenant_grant(policies_dir):
    declared = load_policies(policies_dir)["wuwork"]
    assert "aura" in declared.cross_tenant_read

    effective = compose(
        declared,
        [Override(tenant="wuwork", field="cross_tenant_read", removed_value="aura")],
    )

    assert "aura" not in effective.cross_tenant_read
    assert "helpmate" in effective.cross_tenant_read


def test_compose_switches_fallback_off_and_disables_a_tenant(policies_dir):
    declared = load_policies(policies_dir)["wuwork"]
    effective = compose(
        declared,
        [
            Override(tenant="wuwork", field="allow_fallback", removed_value="true"),
            Override(tenant="wuwork", field="enabled", removed_value="true"),
        ],
    )
    assert effective.allow_fallback is False
    assert effective.enabled is False


def test_compose_replaces_the_budget_when_one_is_given(policies_dir):
    # Budget is an argument, not an override, because it can go up. That is
    # its only difference from the four fields above, and the whole reason it
    # was kept out of Override.
    declared = load_policies(policies_dir)["wuwork"]
    raised = compose(declared, [], budget=declared.budget_nanousd_per_day * 3)
    assert raised.budget_nanousd_per_day == declared.budget_nanousd_per_day * 3


def test_compose_ignores_overrides_for_other_tenants_and_absent_models(policies_dir):
    declared = load_policies(policies_dir)["wuwork"]

    assert (
        compose(
            declared,
            [
                Override(
                    tenant="helpmate", field="cross_tenant_read", removed_value="aura"
                )
            ],
        )
        == declared
    )

    # Orphan override: the model left the policy but the override remains.
    assert (
        compose(
            declared,
            [
                Override(
                    tenant="wuwork",
                    field="substitutable_to",
                    removed_value="qwen3",
                    model="vendor/model-that-left",
                )
            ],
        )
        == declared
    )


# --- exhaustive monotonicity ---


def _every_removable(policy):
    for model, mp in policy.models.items():
        for family in mp.substitutable_to:
            yield Override(
                tenant=policy.tenant,
                field="substitutable_to",
                removed_value=family,
                model=model,
            )
    for target in policy.cross_tenant_read:
        yield Override(
            tenant=policy.tenant, field="cross_tenant_read", removed_value=target
        )
    yield Override(tenant=policy.tenant, field="allow_fallback", removed_value="true")
    yield Override(tenant=policy.tenant, field="enabled", removed_value="true")


def _adversarial(policy):
    """Overrides naming values the policy never declared.

    Removing something that is already absent must be a no-op. Without these
    the monotonicity check is blind to the mutation it exists to catch: an
    implementation that *appends* `removed_value` instead of dropping it
    still satisfies `composed <= declared` as long as every value fed to it
    was already a member. Found by running exactly that mutation.
    """
    yield Override(
        tenant=policy.tenant, field="cross_tenant_read", removed_value="ghost-tenant"
    )
    for model in policy.models:
        yield Override(
            tenant=policy.tenant,
            field="substitutable_to",
            removed_value="ghost-family",
            model=model,
        )


def _assert_not_wider(effective, declared):
    assert set(effective.cross_tenant_read) <= set(declared.cross_tenant_read)
    assert effective.allow_fallback <= declared.allow_fallback
    assert effective.enabled <= declared.enabled
    assert set(effective.models) == set(declared.models)
    for model, mp in effective.models.items():
        assert set(mp.substitutable_to) <= set(declared.models[model].substitutable_to)


def test_no_override_combination_widens_anything(policies_dir):
    # Exhaustive rather than hypothesis: the space is five tenants with a few
    # models each, and enumerating the real policies/ covers the actual
    # subject better than random generation would. It also keeps the test
    # dependencies at zero.
    for declared in load_policies(policies_dir).values():
        every = [*_every_removable(declared), *_adversarial(declared)]
        for ov in every:
            _assert_not_wider(compose(declared, [ov]), declared)
        # The strongest single case: everything at once.
        _assert_not_wider(compose(declared, every), declared)


def test_removing_something_the_policy_never_declared_is_a_no_op(policies_dir):
    # The other half of the same point: an override naming an absent value
    # must leave the policy untouched, not quietly introduce that value.
    for declared in load_policies(policies_dir).values():
        for ov in _adversarial(declared):
            assert compose(declared, [ov]) == declared


def test_applying_the_same_override_twice_changes_nothing(policies_dir):
    # Idempotent. The control plane replays every override on every restart.
    for declared in load_policies(policies_dir).values():
        for ov in _every_removable(declared):
            assert compose(declared, [ov]) == compose(declared, [ov, ov])


# --- assembly entry point ---


def test_with_no_overrides_the_effective_policies_equal_the_declared_ones(policies_dir):
    # Phase 4a's completion criterion, in one assertion.
    assert load_effective_policies(policies_dir) == load_policies(policies_dir)


def test_overrides_and_budgets_reach_only_the_tenant_they_name(policies_dir):
    declared = load_policies(policies_dir)
    effective = load_effective_policies(
        policies_dir,
        overrides=[
            Override(tenant="wuwork", field="cross_tenant_read", removed_value="aura")
        ],
        budgets={"aura": 42},
    )

    assert "aura" not in effective["wuwork"].cross_tenant_read
    assert effective["aura"].budget_nanousd_per_day == 42
    assert effective["helpmate"] == declared["helpmate"]

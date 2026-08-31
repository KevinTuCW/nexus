"""Effective tenant policy: the declared value narrowed by control-plane overrides.

This layer exists for its position, not its logic. Policy fields have 11
direct read sites across 7 modules, and two of them --
`budget_nanousd_per_day` and `allow_fallback` -- are bare attributes with no
funnel function. Resolving overrides at read time would mean editing all 11
and remembering to edit the 12th, and forgetting one looks exactly like the
failure this repository already found once: a boundary that is real on the
path people examine and absent on the path people use.

So overrides are not resolved at read time. They are composed before `State`
is assembled. By the time anything can read a policy it is already the
effective one, and forgetting a path is structurally impossible.

This layer only subtracts. `Override` has no `new_value`, so loosening has
no syntax here -- it can only be done by editing YAML and going through
review. Budget is the single exception and is passed as its own argument
rather than disguised as an override, because it must be able to go up.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from nexus.registry.tenants import ModelPolicy, TenantPolicy, load_policies

#: Fields an override may narrow. Budget is deliberately absent -- see above.
CAPABILITY_FIELDS = (
    "substitutable_to",
    "cross_tenant_read",
    "allow_fallback",
    "enabled",
)


@dataclass(frozen=True)
class Override:
    """A record of what was removed. There is no "set to"."""

    tenant: str
    field: str
    removed_value: str
    #: Only for substitutable_to: which model's table this hangs under.
    model: str | None = None

    def __post_init__(self) -> None:
        if self.field not in CAPABILITY_FIELDS:
            raise ValueError(
                f"'{self.field}' is not a capability field. This layer can "
                f"only narrow {CAPABILITY_FIELDS}; budget lives in its own "
                "table because it must be able to go up."
            )
        if self.field == "substitutable_to" and self.model is None:
            raise ValueError(
                "a substitutable_to override must name the model it hangs "
                "under; without it there is no table to remove from"
            )


def compose(
    declared: TenantPolicy,
    overrides: Sequence[Override] = (),
    budget: int | None = None,
) -> TenantPolicy:
    """Declared policy narrowed by overrides. Budget replaces rather than narrows.

    The two are separate arguments because they behave differently:
    capabilities can only shrink, budget can move either way. Folding budget
    into `overrides` would break the monotonicity property the tests pin, and
    an assertion that is necessarily false eventually gets relaxed rather
    than fixed.
    """
    models = dict(declared.models)
    cross = list(declared.cross_tenant_read)
    allow_fallback = declared.allow_fallback
    enabled = declared.enabled

    for ov in overrides:
        if ov.tenant != declared.tenant:
            continue
        if ov.field == "substitutable_to":
            current = models.get(ov.model)
            if current is None:
                # Orphan: names a model the policy no longer declares. It has
                # no effect and is not an error; listing orphans is the
                # console's job in Phase 4c.
                continue
            models[ov.model] = ModelPolicy(
                substitutable_to=tuple(
                    f for f in current.substitutable_to if f != ov.removed_value
                )
            )
        elif ov.field == "cross_tenant_read":
            cross = [t for t in cross if t != ov.removed_value]
        elif ov.field == "allow_fallback":
            # Booleans have one direction to be removed in; "removing false"
            # means nothing, so `removed_value` is not consulted.
            allow_fallback = False
        elif ov.field == "enabled":
            enabled = False

    return replace(
        declared,
        models=models,
        cross_tenant_read=tuple(cross),
        allow_fallback=allow_fallback,
        enabled=enabled,
        budget_nanousd_per_day=(
            declared.budget_nanousd_per_day if budget is None else budget
        ),
    )


def load_effective_policies(
    policies_dir: Path,
    overrides: Sequence[Override] = (),
    budgets: dict[str, int] | None = None,
) -> dict[str, TenantPolicy]:
    """The single entry point both assembly sites use.

    `state.py` and `eval.py` both call this. Each calling `load_policies` and
    composing separately would be two implementations, and this repository
    has already paid for that once: the authorisation rule was real in
    `/v1/usage` and absent in the console.
    """
    budgets = budgets or {}
    return {
        name: compose(
            declared,
            [o for o in overrides if o.tenant == name],
            budgets.get(name),
        )
        for name, declared in load_policies(policies_dir).items()
    }

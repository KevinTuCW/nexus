"""The loosening channel: a diff and the gates' verdict on it, never a write.

Tightening is hot because it can only ever refuse more. Loosening is the
direction the four gates exist to catch, so it does not get a write path at
all -- the control plane produces a diff against `policies/<tenant>.yaml`,
runs G1 and G4 against what the policy *would* become, and hands both to a
human to land through review.

That is maker-checker without a second workflow to build: the proposer
generates it here, the reviewer approves it in the pull request. git already
is that process, and the record it leaves is harder to bypass than a bespoke
approvals table would be.

Nothing in this module touches the database. A proposal that quietly wrote
itself somewhere would be the loosening path this design says does not exist.
"""

from dataclasses import replace

import yaml

from nexus.eval import (
    check_g1_diversity_is_never_collapsed,
    check_g4_fallback_is_never_silent,
)
from nexus.registry.tenants import ModelPolicy, TenantPolicy

#: What a proposal may ask for. Mirrors the capability fields, plus budget,
#: which is here only because raising past the threshold is also a loosening.
LOOSENABLE = ("substitutable_to", "cross_tenant_read", "allow_fallback")


def widen(
    declared: TenantPolicy, field: str, value: str, model: str | None = None
) -> TenantPolicy:
    """The policy `declared` would become if the request were granted."""
    if field == "substitutable_to":
        if model is None:
            raise ValueError("a substitutable_to proposal must name a model")
        models = dict(declared.models)
        current = models.get(model, ModelPolicy())
        if value in current.substitutable_to:
            return declared
        models[model] = ModelPolicy(
            substitutable_to=(*current.substitutable_to, value)
        )
        return replace(declared, models=models)
    if field == "cross_tenant_read":
        if value in declared.cross_tenant_read:
            return declared
        return replace(
            declared, cross_tenant_read=(*declared.cross_tenant_read, value)
        )
    if field == "allow_fallback":
        return replace(declared, allow_fallback=True)
    raise ValueError(f"'{field}' is not a loosenable field; expected {LOOSENABLE}")


def to_yaml(policy: TenantPolicy) -> str:
    """Render a policy the way `policies/<tenant>.yaml` is written."""
    body: dict = {
        "tenant": policy.tenant,
        "integration": policy.integration,
        "repo_path": str(policy.repo_path) if policy.repo_path else None,
        "gate_command": policy.gate_command,
        "api_key_env": policy.api_key_env,
        "allow_fallback": policy.allow_fallback,
        "budget_nanousd_per_day": policy.budget_nanousd_per_day,
    }
    if policy.cross_tenant_read:
        body["cross_tenant_read"] = list(policy.cross_tenant_read)
    if not policy.enabled:
        body["enabled"] = False
    if policy.models:
        body["models"] = {
            name: {"substitutable_to": list(mp.substitutable_to)}
            for name, mp in policy.models.items()
        }
    return yaml.safe_dump(body, allow_unicode=True, sort_keys=False)


def unified_diff(before: TenantPolicy, after: TenantPolicy) -> str:
    """A diff a reviewer can read, in the file's own format."""
    import difflib

    return "".join(
        difflib.unified_diff(
            to_yaml(before).splitlines(keepends=True),
            to_yaml(after).splitlines(keepends=True),
            fromfile=f"policies/{before.tenant}.yaml",
            tofile=f"policies/{after.tenant}.yaml (proposed)",
        )
    )


def gate_evidence(rows, policies: dict[str, TenantPolicy]) -> dict:
    """Run G1 and G4 against the policy the proposal would create.

    Three outcomes, not two. A proposal judged against an empty ledger has
    not passed anything -- it has been asked a question with no evidence to
    answer it, and this repository already refuses to print `passed` for
    that case in `nexus.eval`. The same refusal belongs here, where somebody
    is about to widen a constraint on the strength of what it says.
    """
    if not rows:
        return {
            "verdict": "no_evidence",
            "detail": (
                "账本里没有可判的调用。这不是通过——是没有证据可以据以放行。"
            ),
            "g1": [],
            "g4": [],
        }
    g1 = check_g1_diversity_is_never_collapsed(rows, policies)
    g4 = check_g4_fallback_is_never_silent(rows)
    return {
        "verdict": "would_violate" if (g1 or g4) else "clean",
        "detail": (
            "在现有账本上，放开之后 G1/G4 仍然干净。这只说明历史流量不违规，"
            "不保证未来流量不会——放开之后能做的事变多了。"
            if not (g1 or g4)
            else "放开之后，现有账本里已经存在会违反硬门的调用。"
        ),
        "g1": g1,
        "g4": g4,
    }


def build(
    declared: TenantPolicy,
    field: str,
    value: str,
    model: str | None,
    rows,
    all_policies: dict[str, TenantPolicy],
) -> dict:
    """A complete proposal: what changes, and what the gates say about it."""
    after = widen(declared, field, value, model)
    proposed = dict(all_policies)
    proposed[declared.tenant] = after
    return {
        "tenant": declared.tenant,
        "field": field,
        "value": value,
        "model": model,
        "diff": unified_diff(declared, after),
        "no_op": after == declared,
        "gates": gate_evidence(rows, proposed),
        "how_to_apply": (
            f"这是提案，不是改动——控制面没有写入放松的路径。把上面的 diff "
            f"落到 policies/{declared.tenant}.yaml，走 review 合入，再重启网关。"
        ),
    }

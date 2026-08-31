"""Tenant policies, loaded from policies/<tenant>.yaml.

A tenant policy is the *only* place a substitution permission can be
granted. The default is deny: a model the policy does not mention cannot be
swapped by the router. Default-allow would be the natural-feeling choice and
the wrong one — forgetting to configure a model would hand the router
permission to replace it, and the resulting quality regression surfaces
weeks later with nothing in the logs pointing back here.

Policies live on the nexus side rather than in the request because the four
incumbent tenants are integrated zero-touch: their repos are not modified,
so they cannot attach constraints to their own calls. Native tenants
(wuwork) can declare per-request constraints as well; that difference is
real and is displayed as such, not smoothed over.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml


@dataclass(frozen=True)
class ModelPolicy:
    #: Weight families this model may be substituted with. Empty = pinned.
    substitutable_to: tuple[str, ...] = ()


@dataclass(frozen=True)
class TenantPolicy:
    tenant: str
    integration: Literal["zero_touch", "native"]
    repo_path: Path | None
    gate_command: str
    api_key_env: str
    allow_fallback: bool
    budget_nanousd_per_day: int
    #: Tenants whose usage this tenant may read. Empty by default, and the
    #: default is the point: "reuse" as a platform selling point must not
    #: rest on a boundary crossing nobody signed off. The first genuine
    #: reuse request is also the first request to leave its own tenant.
    cross_tenant_read: tuple[str, ...] = ()
    #: Whether this tenant may reach the gateway at all. Declared here so a
    #: policy file can ship a tenant switched off, and narrowed by the
    #: control plane in Phase 4b. Default True: a missing field is a tenant
    #: nobody disabled, not a tenant nobody enabled.
    enabled: bool = True
    models: dict[str, ModelPolicy] = field(default_factory=dict)


def load_policies(policies_dir: Path) -> dict[str, TenantPolicy]:
    """Load every policies/*.yaml into a name -> policy mapping."""
    registry: dict[str, TenantPolicy] = {}
    for path in sorted(policies_dir.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        models = {
            model: ModelPolicy(
                substitutable_to=tuple((cfg or {}).get("substitutable_to", ()))
            )
            for model, cfg in (raw.get("models") or {}).items()
        }
        repo_raw = raw.get("repo_path")
        policy = TenantPolicy(
            tenant=raw["tenant"],
            integration=raw["integration"],
            repo_path=Path(repo_raw).expanduser() if repo_raw else None,
            gate_command=raw["gate_command"],
            api_key_env=raw["api_key_env"],
            allow_fallback=bool(raw["allow_fallback"]),
            budget_nanousd_per_day=int(raw["budget_nanousd_per_day"]),
            cross_tenant_read=tuple(raw.get("cross_tenant_read", ())),
            enabled=bool(raw.get("enabled", True)),
            models=models,
        )
        if policy.tenant != path.stem:
            raise ValueError(
                f"{path}: declares tenant '{policy.tenant}' but is named "
                f"'{path.stem}.yaml'; the mismatch would make the file "
                "unreachable by name lookup"
            )
        registry[policy.tenant] = policy
    return registry


def substitution_allowed(policy: TenantPolicy, model: str, target_family: str) -> bool:
    """May the router serve `model` from `target_family` instead?

    Default deny: an unlisted model is pinned.
    """
    model_policy = policy.models.get(model)
    if model_policy is None:
        return False
    return target_family in model_policy.substitutable_to

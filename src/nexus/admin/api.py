"""The control plane's HTTP surface.

Two channels, and which one a change takes is decided by direction rather
than by field:

  - **Tightening and operations** are hot. Issuing and revoking credentials,
    switching a tenant off, removing a substitution, lowering a budget --
    all of these can only ever cause more to be refused, so they are written,
    recomposed in place, and audited.
  - **Loosening** has no write path here at all. `/admin/proposals` returns a
    diff against `policies/<tenant>.yaml` plus what G1 and G4 say about it,
    and a human lands it through review. The storage layer cannot express a
    widening -- `policy_override` has no `new_value` column -- so this is not
    a rule that could be forgotten, it is one that has no syntax.

Budget is the exception that proves the split is about direction and not
about danger. Raising a budget is a loosening, but it has no gate behind it
and it is a weekly operational act; sending it through review would push
operators to bypass the whole control plane at 2am. It stays hot, with a
threshold above which a second administrator has to sign.

Every write recomposes in place and never calls `get_state.cache_clear()`.
Clearing rebuilds `State`, discarding `RoutingLog` -- the record of what G1
vetoed, and the only reason the console's routing panel has anything in it.
"""

from pathlib import Path

from fastapi import APIRouter, Body, Header, HTTPException, Query
from fastapi.responses import FileResponse

from nexus.admin import proposal
from nexus.admin.store import ControlPlaneStore, TenantKeyStore
from nexus.config import get_settings
from nexus.ingress.admin_auth import Admin, AdminAuthError, authenticate_admin
from nexus.registry.effective import CAPABILITY_FIELDS, Override
from nexus.state import get_state

router = APIRouter()


def _admin(authorization: str) -> Admin:
    """Authenticate an administrator, or refuse.

    403 rather than 401 for a valid tenant credential: the caller proved who
    they are and the answer is still no. Saying 401 would invite them to
    retry with the same key, which will never work.
    """
    state = get_state()
    try:
        return authenticate_admin(authorization, state.admin_index)
    except AdminAuthError as exc:
        try:
            from nexus.ingress.auth import authenticate

            tenant = authenticate(authorization, state.key_index)
        except Exception:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        raise HTTPException(
            status_code=403,
            detail=(
                f"'{tenant}' is a tenant credential. The control plane is a "
                "separate namespace; a data-plane key never carries "
                "administrative power."
            ),
        ) from exc


def _writer(authorization: str) -> Admin:
    admin = _admin(authorization)
    if not admin.may_write:
        raise HTTPException(
            status_code=403, detail=f"administrator '{admin.name}' is read-only"
        )
    return admin


def _stores() -> tuple[ControlPlaneStore, TenantKeyStore]:
    dsn = get_settings().database_url
    return ControlPlaneStore(dsn), TenantKeyStore(dsn)


def _check_version(cp: ControlPlaneStore, tenant: str, expected: str | None) -> None:
    """Optimistic concurrency across both mutable tables.

    Two administrators editing one tenant at once is not an edge case at this
    size; it is Tuesday afternoon. The version spans overrides *and* budgets
    because looking at one table lets the person editing the other believe
    they hold current state.
    """
    if expected is None:
        return
    actual = cp.policy_version(tenant)
    if expected != actual:
        raise HTTPException(
            status_code=409,
            detail=(
                f"另一位管理员刚改过 '{tenant}'（版本 {expected} → {actual}）。"
                "请重新加载后再提交。"
            ),
        )


def _refresh(tenant: str) -> None:
    """Recompose one tenant in place from what the tables now say."""
    cp, keys = _stores()
    state = get_state()
    state.recompose(
        tenant,
        [o for o in cp.active_overrides() if o.tenant == tenant],
        cp.current_budgets().get(tenant),
    )
    # Credentials can change in the same breath as policy (issuing a key for a
    # tenant you just re-enabled), so the index is refreshed with it.
    state.key_index.clear()
    from nexus.ingress.auth import build_key_index

    state.key_index.update(build_key_index(state.policies, keys.active_digests()))


def _policy_snapshot(tenant: str) -> dict:
    p = get_state().policies[tenant]
    return {
        "enabled": p.enabled,
        "allow_fallback": p.allow_fallback,
        "budget_nanousd_per_day": p.budget_nanousd_per_day,
        "cross_tenant_read": list(p.cross_tenant_read),
        "models": {m: list(mp.substitutable_to) for m, mp in p.models.items()},
    }


# --- identity ------------------------------------------------------------


@router.get("/admin/whoami")
def whoami(authorization: str = Header(default="")) -> dict:
    """Who the control plane thinks you are. Named, never "admin"."""
    admin = _admin(authorization)
    return {"admin": admin.name, "role": admin.role}


# --- tenants -------------------------------------------------------------


@router.get("/admin/tenants")
def tenants(authorization: str = Header(default="")) -> dict:
    """Declared and effective, side by side.

    Side by side because either alone misleads. Only the effective value
    cannot answer "was it always so, or did somebody tighten it"; only the
    declared value is a screen that lies about what the gateway is doing.
    """
    _admin(authorization)
    cp, keys = _stores()
    state = get_state()
    overrides = cp.list_overrides()
    rows = []
    for name in sorted(state.declared):
        declared, effective = state.declared[name], state.policies[name]
        rows.append(
            {
                "tenant": name,
                "integration": declared.integration,
                "gate_command": declared.gate_command,
                "enabled": effective.enabled,
                "declared": {
                    "allow_fallback": declared.allow_fallback,
                    "budget_nanousd_per_day": declared.budget_nanousd_per_day,
                    "cross_tenant_read": list(declared.cross_tenant_read),
                    "models": {
                        m: list(mp.substitutable_to)
                        for m, mp in declared.models.items()
                    },
                },
                "effective": {
                    "allow_fallback": effective.allow_fallback,
                    "budget_nanousd_per_day": effective.budget_nanousd_per_day,
                    "cross_tenant_read": list(effective.cross_tenant_read),
                    "models": {
                        m: list(mp.substitutable_to)
                        for m, mp in effective.models.items()
                    },
                },
                "overrides_in_force": [
                    o for o in overrides
                    if o["tenant"] == name and o["state"] == "in_force"
                ],
                "version": cp.policy_version(name),
                "keys": len(
                    [k for k in keys.list_for_console(name) if k["state"] == "active"]
                ),
            }
        )
    return {"tenants": rows, "orphans": orphan_overrides()}


def orphan_overrides() -> list[dict]:
    """Overrides that no longer bite, because the YAML moved under them.

    Somebody removed the value from `policies/<tenant>.yaml` while an
    override removing the same value was still in force. The override now
    does nothing but still reads as "in force", which is a lie of exactly the
    kind this project keeps refusing to ship.

    Listed, never auto-cleaned. Cleaning silently would make "the pull
    request landed but had no effect" and "somebody's tightening vanished"
    both happen with nothing on screen.
    """
    state = get_state()
    cp, _ = _stores()
    out = []
    for o in cp.list_overrides():
        if o["state"] != "in_force":
            continue
        declared = state.declared.get(o["tenant"])
        if declared is None:
            out.append({**o, "why": "策略文件里已经没有这个租户"})
            continue
        if o["field"] == "substitutable_to":
            mp = declared.models.get(o["model"])
            if mp is None:
                out.append({**o, "why": f"声明里已无模型 {o['model']}"})
            elif o["removed_value"] not in mp.substitutable_to:
                out.append(
                    {**o, "why": f"声明里已无 {o['removed_value']}，这条覆盖不再起作用"}
                )
        elif o["field"] == "cross_tenant_read":
            if o["removed_value"] not in declared.cross_tenant_read:
                out.append(
                    {**o, "why": f"声明里已无授权 {o['removed_value']}"}
                )
        elif o["field"] == "allow_fallback" and not declared.allow_fallback:
            out.append({**o, "why": "声明里 allow_fallback 已经是 false"})
    return out


# --- tightening (hot) ----------------------------------------------------


@router.post("/admin/overrides")
def add_override(
    authorization: str = Header(default=""), body: dict = Body(...)
) -> dict:
    """Remove one capability. Takes effect immediately."""
    admin = _writer(authorization)
    tenant = body.get("tenant", "")
    field = body.get("field", "")
    reason = (body.get("reason") or "").strip()

    state = get_state()
    if tenant not in state.declared:
        raise HTTPException(status_code=404, detail=f"no such tenant '{tenant}'")
    if field not in CAPABILITY_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"'{field}' cannot be narrowed here. This layer only removes "
                f"{CAPABILITY_FIELDS}; loosening goes through /admin/proposals."
            ),
        )
    if not reason:
        # NOT NULL in the schema, and refused here with a sentence rather
        # than a constraint error: a tightening with no stated reason is one
        # nobody dares lift later, so it stops being reversible in practice.
        raise HTTPException(
            status_code=400, detail="reason is required: 没有理由的收紧没人敢撤"
        )

    try:
        ov = Override(
            tenant=tenant,
            field=field,
            removed_value=str(body.get("removed_value", "true")),
            model=body.get("model"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    cp, _ = _stores()
    _check_version(cp, tenant, body.get("version"))
    before = _policy_snapshot(tenant)
    override_id = cp.apply_override(ov, reason, admin.name)
    _refresh(tenant)
    after = _policy_snapshot(tenant)
    cp.record(admin.name, f"override.add.{field}", tenant, before, after)
    return {
        "override_id": override_id,
        "tenant": tenant,
        "effective": after,
        "version": cp.policy_version(tenant),
    }


@router.post("/admin/overrides/{override_id}/lift")
def lift_override(
    override_id: int,
    authorization: str = Header(default=""),
    body: dict = Body(default={}),
) -> dict:
    """The inverse action. Every hot change is required to have one."""
    admin = _writer(authorization)
    cp, _ = _stores()
    match = [o for o in cp.list_overrides() if o["id"] == override_id]
    if not match:
        raise HTTPException(status_code=404, detail=f"no override {override_id}")
    tenant = match[0]["tenant"]

    before = _policy_snapshot(tenant)
    cp.lift_override(override_id, admin.name)
    _refresh(tenant)
    after = _policy_snapshot(tenant)
    cp.record(admin.name, "override.lift", tenant, before, after)
    return {"tenant": tenant, "effective": after, "version": cp.policy_version(tenant)}


@router.post("/admin/budget")
def set_budget(
    authorization: str = Header(default=""), body: dict = Body(...)
) -> dict:
    """Change a budget. Lowering is always free; raising has a threshold."""
    admin = _writer(authorization)
    tenant = body.get("tenant", "")
    reason = (body.get("reason") or "").strip()
    state = get_state()
    if tenant not in state.declared:
        raise HTTPException(status_code=404, detail=f"no such tenant '{tenant}'")
    if not reason:
        raise HTTPException(status_code=400, detail="reason is required")
    try:
        new = int(body["budget_nanousd_per_day"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail="budget_nanousd_per_day must be an integer of nano-USD",
        ) from exc
    if new < 0:
        raise HTTPException(status_code=400, detail="budget cannot be negative")

    settings = get_settings()
    current = state.policies[tenant].budget_nanousd_per_day
    approved_by = body.get("approved_by")
    needs_approval = new > current and (
        new > current * settings.budget_raise_factor_without_approval
        or new > settings.budget_ceiling_without_approval
    )
    if needs_approval:
        if not approved_by:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"把 {tenant} 的预算从 {current} 提到 {new} nano-USD 超过阈值，"
                    "需要第二位管理员复核（approved_by）。调低永远不需要。"
                ),
            )
        if approved_by == admin.name:
            raise HTTPException(
                status_code=403,
                detail="复核人不能是提交人——两只眼睛不是四只眼睛",
            )
        if approved_by not in {a.name for a in get_state().admin_index.values()}:
            raise HTTPException(
                status_code=400, detail=f"'{approved_by}' 不是已知管理员"
            )
    else:
        approved_by = None

    cp, _ = _stores()
    _check_version(cp, tenant, body.get("version"))
    before = _policy_snapshot(tenant)
    cp.set_budget(tenant, new, reason, admin.name, approved_by)
    _refresh(tenant)
    after = _policy_snapshot(tenant)
    cp.record(admin.name, "budget.set", tenant, before, after)
    return {
        "tenant": tenant,
        "effective": after,
        "approved_by": approved_by,
        "version": cp.policy_version(tenant),
    }


# --- credentials ---------------------------------------------------------


@router.get("/admin/keys")
def list_keys(authorization: str = Header(default="")) -> dict:
    """Prefix and status only. Never the secret, and never its digest."""
    _admin(authorization)
    _, keys = _stores()
    return {"keys": keys.list_for_console()}


@router.post("/admin/keys")
def issue_key(
    authorization: str = Header(default=""), body: dict = Body(...)
) -> dict:
    """Mint a credential. The plaintext is in this response and nowhere else."""
    admin = _writer(authorization)
    tenant = body.get("tenant", "")
    if tenant not in get_state().declared:
        raise HTTPException(status_code=404, detail=f"no such tenant '{tenant}'")
    label = (body.get("label") or "").strip()
    if not label:
        raise HTTPException(
            status_code=400,
            detail="label is required: 两把没有标签的 key 在界面上分不出谁是谁",
        )

    cp, keys = _stores()
    key_id, plaintext = keys.issue(tenant, label, admin.name)
    _refresh(tenant)
    # The audit row records that a key was issued, not what it is.
    cp.record(admin.name, "key.issue", tenant, None, {"key_id": key_id, "label": label})
    return {
        "key_id": key_id,
        "tenant": tenant,
        "api_key": plaintext,
        "warning": "这把 key 只显示这一次，库里只有它的哈希。",
    }


@router.post("/admin/keys/{key_id}/revoke")
def revoke_key(key_id: str, authorization: str = Header(default="")) -> dict:
    """Kill a credential. There is deliberately no un-revoke."""
    admin = _writer(authorization)
    cp, keys = _stores()
    match = [k for k in keys.list_for_console() if k["key_id"] == key_id]
    if not match:
        raise HTTPException(status_code=404, detail=f"no key {key_id}")
    tenant = match[0]["tenant"]
    keys.revoke(key_id, admin.name)
    _refresh(tenant)
    cp.record(admin.name, "key.revoke", tenant, {"key_id": key_id}, None)
    return {"key_id": key_id, "tenant": tenant, "state": "revoked"}


# --- loosening (proposal only) -------------------------------------------


@router.post("/admin/proposals")
def propose(authorization: str = Header(default=""), body: dict = Body(...)) -> dict:
    """A diff and the gates' verdict. Writes nothing, by design."""
    _admin(authorization)
    tenant = body.get("tenant", "")
    state = get_state()
    if tenant not in state.declared:
        raise HTTPException(status_code=404, detail=f"no such tenant '{tenant}'")
    try:
        return proposal.build(
            state.declared[tenant],
            body.get("field", ""),
            str(body.get("value", "")),
            body.get("model"),
            state.ledger.entries(),
            state.policies,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --- audit ---------------------------------------------------------------


@router.get("/admin/actions")
def actions(authorization: str = Header(default="")) -> dict:
    """Who changed what. Named actors, never a shared identity."""
    _admin(authorization)
    cp, _ = _stores()
    return {"actions": cp.recent_actions()}


@router.get("/admin")
def admin_page(
    authorization: str = Header(default=""), key: str = Query(default="")
) -> FileResponse:
    """The page shell. Unlike the console's, this one authenticates.

    The console's shell is public because every panel it fetches is not, and
    a page that renders nothing without a key leaks nothing. This page
    carries write controls, so it is not served to an anonymous caller.

    `?key=` is accepted because a browser address bar cannot set a header,
    which is the same reason the console reads one. It is a real weakness --
    query strings reach logs and `Referer` headers -- and it is the price of
    a zero-build page. Worth writing down rather than leaving to be noticed.
    """
    _admin(authorization or f"Bearer {key}")
    return FileResponse(Path(__file__).resolve().parent / "static" / "admin.html")

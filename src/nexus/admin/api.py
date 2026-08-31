"""The control plane's HTTP surface.

Two channels, and which one a change takes is decided by direction rather
than by field:

  - **Tightening and operations** are hot. Issuing and revoking credentials,
    switching a tenant off, removing a substitution, lowering a budget --
    all of these can only ever cause more to be refused, so they are written,
    recomposed in place, and audited.
  - **Loosening** has no write path here at all. `/admin/change-requests`
    files a request, returns the configuration an engineer lands in
    `policies/<tenant>.yaml`, and reports what G1 and G4 say about it. The
    storage layer cannot express a widening -- `policy_override` has no
    `new_value` column -- so this is not a rule that could be forgotten, it
    is one that has no syntax. Filing a request grants nothing, and whether
    it shipped is read back off the policy files rather than off a status
    somebody set.

Budget is the exception that proves the split is about direction and not
about danger. Raising a budget is a loosening, but it has no gate behind it
and it is a weekly operational act; sending it through review would push
operators to bypass the whole control plane at 2am. It stays hot, with a
threshold above which a second administrator has to sign.

Access is by username, password and a server-side session, never a bearer
token. The scheme this replaced could only reach a browser through
`/admin?key=…`, because an address bar cannot set a header -- and a secret in
a query string reaches the access log, the `Referer` of every outbound link,
and browser history.

Every write recomposes in place and never calls `get_state.cache_clear()`.
Clearing rebuilds `State`, discarding `RoutingLog` -- the record of what G1
vetoed, and the only reason the console's routing panel has anything in it.
"""

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Body, Cookie, HTTPException, Response
from fastapi.responses import FileResponse

from nexus.admin import change_request
from nexus.admin.store import (
    ChangeRequestStore,
    ControlPlaneStore,
    TenantKeyStore,
)
from nexus.config import get_settings
from nexus.admin.accounts import (
    COOKIE_NAME,
    SESSION_LIFETIME,
    AccountStore,
    Admin,
    LoginFailed,
)
from nexus.policy.quota import day_start
from nexus.registry.effective import CAPABILITY_FIELDS, Override
from nexus.state import get_state

router = APIRouter()

#: Where `make eval` writes a tenant's recorded baseline. A tenant without one
#: is not "passing" -- it is unchecked, and the overview says so.
BASELINES_DIR = Path(__file__).resolve().parents[3] / "baselines"

STATIC_DIR = Path(__file__).resolve().parent / "static"

#: The page is four files rather than one. Served from a fixed whitelist
#: rather than by joining the request path onto a directory, because the
#: second form is how a static handler turns into an arbitrary file read.
STATIC_FILES = {
    "admin.css": "text/css; charset=utf-8",
    "admin.js": "text/javascript; charset=utf-8",
    "terms.js": "text/javascript; charset=utf-8",
}


def _accounts() -> AccountStore:
    return AccountStore(get_settings().database_url)


def _admin(session: str) -> Admin:
    """Resolve the session cookie to an administrator, or refuse.

    The control plane no longer accepts any bearer credential at all, so a
    tenant key reaching here is simply not a session and gets the same 401
    as an expired one. The two namespaces cannot be confused because only
    one of them has a login.
    """
    admin = _accounts().resolve(session or "")
    if admin is None:
        raise HTTPException(status_code=401, detail="未登录或会话已过期")
    return admin


def _writer(session: str) -> Admin:
    admin = _admin(session)
    if not admin.may_write:
        raise HTTPException(
            status_code=403, detail=f"账号「{admin.name}」是只读权限，不能执行这个操作"
        )
    return admin


def _stores() -> tuple[ControlPlaneStore, TenantKeyStore]:
    dsn = get_settings().database_url
    return ControlPlaneStore(dsn), TenantKeyStore(dsn)


def _requests() -> ChangeRequestStore:
    return ChangeRequestStore(get_settings().database_url)


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


# --- login ---------------------------------------------------------------


@router.post("/admin/login")
def login(response: Response, body: dict = Body(...)) -> dict:
    """Exchange a username and password for a session cookie.

    The cookie is `HttpOnly` so page scripts cannot read it, `SameSite=Strict`
    so another site cannot make an authenticated request on the operator's
    behalf, and `Secure` by default -- browsers treat `http://localhost` as a
    trustworthy origin, so that still works for local development.

    The token never appears in the response body. Putting it there would
    invite somebody to store it, and stored credentials end up in query
    strings, which is the failure this whole change exists to remove.
    """
    settings = get_settings()
    try:
        token, admin = _accounts().login(
            str(body.get("username", "")), str(body.get("password", ""))
        )
    except LoginFailed as exc:
        # One message for unknown account, wrong password, locked and
        # disabled. Distinguishing them hands out a username oracle.
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite="strict",
        secure=settings.admin_cookie_secure,
        max_age=int(SESSION_LIFETIME.total_seconds()),
        path="/",
    )
    return {"admin": admin.name, "role": admin.role}


@router.post("/admin/logout")
def logout(
    response: Response,
    session: str = Cookie(default="", alias=COOKIE_NAME),
) -> dict:
    """End this session server-side, then clear the cookie.

    Server-side first. Clearing only the cookie would leave a live row that
    anyone holding the token could still use, which is the difference between
    logging out and merely looking logged out.
    """
    if session:
        _accounts().logout(session)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


# --- identity ------------------------------------------------------------


@router.get("/admin/whoami")
def whoami(session: str = Cookie(default="", alias="nexus_admin_session")) -> dict:
    """Who the control plane thinks you are. Named, never "admin".

    Carries the budget-approval thresholds too, so the console can tell an
    operator *while they are typing* that the raise they are entering needs a
    second administrator. Without them the page can only find out by being
    refused, after the reason has been written -- and a form that rejects you
    at the end teaches you not to trust it at the start.

    Publishing them to anyone with a session is not a leak: they are a policy
    everybody working here has to know, and the server enforces them
    regardless of what the page believes.
    """
    admin = _admin(session)
    settings = get_settings()
    return {
        "admin": admin.name,
        "role": admin.role,
        "limits": {
            "budget_raise_factor_without_approval": (
                settings.budget_raise_factor_without_approval
            ),
            "budget_ceiling_without_approval": (
                settings.budget_ceiling_without_approval
            ),
        },
    }


# --- tenants -------------------------------------------------------------


@router.get("/admin/tenants")
def tenants(session: str = Cookie(default="", alias="nexus_admin_session")) -> dict:
    """Declared and effective, side by side.

    Side by side because either alone misleads. Only the effective value
    cannot answer "was it always so, or did somebody tighten it"; only the
    declared value is a screen that lies about what the gateway is doing.
    """
    _admin(session)
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
    session: str = Cookie(default="", alias="nexus_admin_session"), body: dict = Body(...)
) -> dict:
    """Remove one capability. Takes effect immediately."""
    admin = _writer(session)
    tenant = body.get("tenant", "")
    field = body.get("field", "")
    reason = (body.get("reason") or "").strip()

    state = get_state()
    if tenant not in state.declared:
        raise HTTPException(status_code=404, detail=f"没有这条业务线：「{tenant}」")
    if field not in CAPABILITY_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"'{field}' cannot be narrowed here. This layer only removes "
                f"{CAPABILITY_FIELDS}; loosening goes through "
                "/admin/change-requests."
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
    session: str = Cookie(default="", alias="nexus_admin_session"),
    body: dict = Body(default={}),
) -> dict:
    """The inverse action. Every hot change is required to have one."""
    admin = _writer(session)
    cp, _ = _stores()
    match = [o for o in cp.list_overrides() if o["id"] == override_id]
    if not match:
        raise HTTPException(status_code=404, detail=f"找不到编号为 {override_id} 的权限收回记录")
    tenant = match[0]["tenant"]

    before = _policy_snapshot(tenant)
    cp.lift_override(override_id, admin.name)
    _refresh(tenant)
    after = _policy_snapshot(tenant)
    cp.record(admin.name, "override.lift", tenant, before, after)
    return {"tenant": tenant, "effective": after, "version": cp.policy_version(tenant)}


@router.post("/admin/budget")
def set_budget(
    session: str = Cookie(default="", alias="nexus_admin_session"), body: dict = Body(...)
) -> dict:
    """Change a budget. Lowering is always free; raising has a threshold."""
    admin = _writer(session)
    tenant = body.get("tenant", "")
    reason = (body.get("reason") or "").strip()
    state = get_state()
    if tenant not in state.declared:
        raise HTTPException(status_code=404, detail=f"没有这条业务线：「{tenant}」")
    if not reason:
        raise HTTPException(status_code=400, detail="必须写明理由")
    try:
        new = int(body["budget_nanousd_per_day"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail="日额度必须是整数（单位 nano-USD）",
        ) from exc
    if new < 0:
        raise HTTPException(status_code=400, detail="日额度不能是负数")

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
        # The approver has to be a real, writable, live account. A free-text
        # name would make the second signature a formality anyone could type.
        approvers = {
            a["username"]
            for a in _accounts().list_accounts()
            if a["state"] == "active" and a["role"] == "rw"
        }
        if approved_by not in approvers:
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
def list_keys(session: str = Cookie(default="", alias="nexus_admin_session")) -> dict:
    """Prefix and status only. Never the secret, and never its digest."""
    _admin(session)
    _, keys = _stores()
    return {"keys": keys.list_for_console()}


@router.post("/admin/keys")
def issue_key(
    session: str = Cookie(default="", alias="nexus_admin_session"), body: dict = Body(...)
) -> dict:
    """Mint a credential. The plaintext is in this response and nowhere else."""
    admin = _writer(session)
    tenant = body.get("tenant", "")
    if tenant not in get_state().declared:
        raise HTTPException(status_code=404, detail=f"没有这条业务线：「{tenant}」")
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
def revoke_key(key_id: str, session: str = Cookie(default="", alias="nexus_admin_session")) -> dict:
    """Kill a credential. There is deliberately no un-revoke."""
    admin = _writer(session)
    cp, keys = _stores()
    match = [k for k in keys.list_for_console() if k["key_id"] == key_id]
    if not match:
        raise HTTPException(status_code=404, detail=f"找不到这把密钥：{key_id}")
    tenant = match[0]["tenant"]
    keys.revoke(key_id, admin.name)
    _refresh(tenant)
    cp.record(admin.name, "key.revoke", tenant, {"key_id": key_id}, None)
    return {"key_id": key_id, "tenant": tenant, "state": "revoked"}


# --- loosening (proposal only) -------------------------------------------


@router.post("/admin/change-requests")
def request_widening(
    session: str = Cookie(default="", alias=COOKIE_NAME), body: dict = Body(...)
) -> dict:
    """Ask to relax a constraint. Files the request; grants nothing.

    The console cannot widen anything at runtime -- `policy_override` has no
    column that could express it. So this produces the configuration an
    engineer lands in `policies/<tenant>.yaml`, plus what G1 and G4 say about it, and files
    a record of who asked and why.

    Filing the request is not granting it. The record exists so a relaxation
    can be found afterwards; whether it took effect is read back off the
    policy files, never off a field somebody set.
    """
    admin = _admin(session)
    tenant = body.get("tenant", "")
    reason = (body.get("reason") or "").strip()
    state = get_state()
    if tenant not in state.declared:
        raise HTTPException(status_code=404, detail=f"没有这个租户：'{tenant}'")
    if not reason:
        raise HTTPException(
            status_code=400, detail="必须写明理由——没有理由的放权没人敢批"
        )
    try:
        built = change_request.build(
            state.declared[tenant],
            body.get("field", ""),
            str(body.get("value", "")),
            body.get("model"),
            state.ledger.entries(),
            state.policies,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    cp, _ = _stores()
    built["id"] = _requests().record(
        tenant=tenant, kind="widen", payload=built["config"], reason=reason,
        requested_by=admin.name, field=body.get("field"),
        model=body.get("model"), value=str(body.get("value", "")),
    )
    cp.record(admin.name, "change_request.open", tenant, None,
              {"field": body.get("field"), "value": body.get("value")})
    return built


@router.post("/admin/change-requests/tenant")
def request_new_tenant(
    session: str = Cookie(default="", alias=COOKIE_NAME), body: dict = Body(...)
) -> dict:
    """A diff that would create a tenant. Writes nothing, by design.

    Creating a tenant is a loosening and it is also unverifiable from here:
    a zero-touch tenant counts as integrated only once
    `scripts/verify_tenant.py` confirms its repo reaches the gateway
    unmodified, which is not something an HTTP handler can establish.
    """
    _admin(session)
    name = (body.get("tenant") or "").strip()
    state = get_state()
    if not name:
        raise HTTPException(status_code=400, detail="必须填写业务线名")
    if name in state.declared:
        raise HTTPException(status_code=409, detail=f"业务线「{name}」已经存在")

    try:
        after = change_request.new_tenant(
            tenant=name,
            integration=body.get("integration", "zero_touch"),
            gate_command=(body.get("gate_command") or "make eval").strip(),
            api_key_env=(
                body.get("api_key_env") or f"NEXUS_KEY_{name.upper()}"
            ).strip(),
            budget_nanousd_per_day=int(body.get("budget_nanousd_per_day") or 0),
            allow_fallback=bool(body.get("allow_fallback")),
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload = change_request.to_yaml(after)
    admin = _admin(session)
    cp, _ = _stores()
    request_id = _requests().record(
        tenant=name, kind="new_tenant", payload=payload,
        reason=(body.get("reason") or "新增业务线").strip(),
        requested_by=admin.name,
    )
    cp.record(admin.name, "change_request.open", name, None, {"kind": "new_tenant"})
    return {
        "id": request_id,
        "tenant": name,
        "config": payload,
        "path": f"policies/{name}.yaml",
        "gates": {
            "verdict": "not_applicable",
            "detail": (
                "新业务线在账目里还没有任何调用，四道红线无从判定。接入是否成立由 "
                "scripts/verify_tenant.py 判断——它要确认对方的代码仓一行未改就能打通。"
            ),
            "g1": [], "g4": [],
        },
        "how_to_apply": (
            f"把上面的配置交给工程侧存成 policies/{name}.yaml，配好 "
            f"{body.get('api_key_env') or f'NEXUS_KEY_{name.upper()}'}，"
            "评审合入后重启网关，再跑 scripts/verify_tenant.py 验收接入。"
        ),
    }


@router.get("/admin/change-requests")
def list_change_requests(
    session: str = Cookie(default="", alias=COOKIE_NAME)
) -> dict:
    """Filed requests, each with a status read back off the policy files.

    Nobody marks one done. If the value is in `policies/<tenant>.yaml` it
    shipped; if it is not, it is still waiting. A status somebody can set is
    a status that will eventually be wrong.
    """
    _admin(session)
    return {"requests": _requests().list_requests(get_state().declared)}


# --- administrators ------------------------------------------------------


@router.get("/admin/accounts")
def list_accounts(session: str = Cookie(default="", alias=COOKIE_NAME)) -> dict:
    """Never returns a hash or a salt."""
    _admin(session)
    return {"accounts": _accounts().list_accounts()}


@router.post("/admin/accounts")
def create_account(
    session: str = Cookie(default="", alias=COOKIE_NAME), body: dict = Body(...)
) -> dict:
    """Create another administrator.

    The *first* administrator still cannot be created this way -- there is no
    session to authenticate, and a control plane that mints its own first
    administrator over HTTP has a window in which anyone can be that
    administrator. Subsequent ones have no such window: somebody already
    authenticated is vouching.
    """
    admin = _writer(session)
    cp, _ = _stores()
    try:
        _accounts().create(
            str(body.get("username", "")).strip(),
            str(body.get("password", "")),
            body.get("role", "rw"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"无法创建：{exc}") from exc
    username = str(body.get("username", "")).strip()
    cp.record(admin.name, "account.create", username, None, {"role": body.get("role")})
    return {"username": username, "role": body.get("role", "rw")}


@router.post("/admin/accounts/{username}/disable")
def disable_account(
    username: str, session: str = Cookie(default="", alias=COOKIE_NAME)
) -> dict:
    """Disable an account and end its live sessions in the same breath."""
    admin = _writer(session)
    if username == admin.name:
        raise HTTPException(
            status_code=400,
            detail="不能停用自己——那会把最后一个管理员锁在门外，且无法自救",
        )
    cp, _ = _stores()
    _accounts().disable(username)
    cp.record(admin.name, "account.disable", username, None, None)
    return {"username": username, "state": "disabled"}


@router.post("/admin/accounts/{username}/enable")
def enable_account(
    username: str, session: str = Cookie(default="", alias=COOKIE_NAME)
) -> dict:
    """Let a disabled account log in again.

    Every hot action in this control plane is required to have an inverse,
    and disabling was the one that did not: reversing a mistyped username
    meant an UPDATE against the table by hand. Live sessions are deliberately
    not revived -- re-enabling means "may log in again", not "the tokens from
    before are valid again".
    """
    admin = _writer(session)
    store = _accounts()
    if username not in {a["username"] for a in store.list_accounts()}:
        raise HTTPException(status_code=404, detail=f"没有这个账号：'{username}'")
    cp, _ = _stores()
    store.enable(username)
    cp.record(admin.name, "account.enable", username, None, {"state": "active"})
    return {"username": username, "state": "active"}


@router.post("/admin/accounts/{username}/unlock")
def unlock_account(
    username: str, session: str = Cookie(default="", alias=COOKIE_NAME)
) -> dict:
    """Clear a failed-login lockout. Does not touch the password or `disabled`."""
    admin = _writer(session)
    store = _accounts()
    row = [a for a in store.list_accounts() if a["username"] == username]
    if not row:
        raise HTTPException(status_code=404, detail=f"没有这个账号：'{username}'")
    if row[0]["state"] == "disabled":
        raise HTTPException(
            status_code=409,
            detail=f"'{username}' 是被停用，不是被冻结——请用「恢复账号」",
        )
    cp, _ = _stores()
    store.unlock(username)
    cp.record(admin.name, "account.unlock", username, None, None)
    return {"username": username, "state": "active"}


@router.post("/admin/password")
def change_own_password(
    session: str = Cookie(default="", alias=COOKIE_NAME), body: dict = Body(...)
) -> dict:
    """Change your own password. Requires the current one.

    Only your own, on purpose. Letting one `rw` administrator reset another's
    password would let them set it to a value they know and then sign in as
    that person -- which quietly dissolves the two-administrator rule on
    budget raises. Somebody who has genuinely lost a password gets a new
    account, or a reset from the shell where physical access is the check.

    Read-only administrators may use this: refusing would mean their password
    can only ever be changed by someone else, which is worse.
    """
    admin = _admin(session)
    current = str(body.get("current_password", ""))
    new = str(body.get("new_password", ""))
    store = _accounts()

    if not store.verify(admin.name, current):
        # Not routed through `login()`: counting a typo here towards the
        # lockout would freeze the account whose session is making the call.
        raise HTTPException(status_code=403, detail="当前密码不正确")
    if new == current:
        raise HTTPException(status_code=400, detail="新密码和当前密码相同")
    try:
        store.set_password(admin.name, new)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # The reason to change a password under suspicion is to evict whoever
    # else holds it. Leaving their sessions live means nothing happens for
    # up to twelve hours.
    ended = store.revoke_other_sessions(admin.name, session)
    cp, _ = _stores()
    cp.record(admin.name, "password.change", admin.name, None, {"sessions_ended": ended})
    return {"username": admin.name, "sessions_ended": ended}


# --- audit ---------------------------------------------------------------


@router.get("/admin/actions")
def actions(session: str = Cookie(default="", alias="nexus_admin_session")) -> dict:
    """Who changed what. Named actors, never a shared identity."""
    _admin(session)
    cp, _ = _stores()
    return {"actions": cp.recent_actions()}


# --- overview ------------------------------------------------------------


def _aware(ts: datetime) -> datetime:
    """Treat a naive timestamp as UTC rather than crashing the whole panel.

    `PgLedger` returns `TIMESTAMPTZ` and is always aware; `InMemoryLedger`
    stores whatever it was handed. Comparing the two raises `TypeError`, and
    an overview that 500s because one row was naive is worse than one that
    assumes the gateway's own clock.
    """
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


@router.get("/admin/overview")
def overview(session: str = Cookie(default="", alias=COOKIE_NAME)) -> dict:
    """Everything the front page needs, in one round trip.

    The five `/console` panels already compute most of this, but they
    authenticate with a *tenant* credential and trim to that tenant's
    `cross_tenant_read` scope. A control-plane session is neither, and
    widening the console's authorisation so the console could serve this
    would put a second definition of "who may see whom" next to
    `ingress/authz.py`. A rule with two implementations is two rules, and the
    loose one wins.

    **Two clocks, and they are labelled separately in the response.** Anything
    derived from the ledger is scoped to today on the same `day_start` the
    gateway enforces 429s on -- `/console/quota` learned that the hard way,
    having once compared a month of spend against a daily allowance. Routing
    vetoes and their reasons come from `RoutingLog`, which is a bounded
    in-process deque that a restart empties, so those carry
    `window: "since_boot"`. Reporting both as "today" would make a restart
    look like the anomalies went away.
    """
    _admin(session)
    state = get_state()
    cp, keys = _stores()
    settings = get_settings()

    now = datetime.now(timezone.utc)
    since = day_start(now)
    entries = [e for e in state.ledger.entries() if _aware(e.ts) >= since]

    # Money comes from `spent_since`, not from summing the rows above: it is
    # the same call the request path checks a budget against, so the panel
    # and the 429 cannot disagree.
    spend = {n: state.ledger.spent_since(n, since) for n in state.policies}

    online = [n for n, p in state.policies.items() if p.enabled]
    switched_off = [n for n, p in state.policies.items() if not p.enabled]
    over = [
        n for n, p in state.policies.items()
        if p.budget_nanousd_per_day > 0 and spend[n] >= p.budget_nanousd_per_day
    ]
    zero_budget = [
        n for n, p in state.policies.items() if p.budget_nanousd_per_day == 0
    ]

    vetoed = [e for e in state.routing.events() if e.vetoed]
    fallbacks = [e for e in entries if e.fallback_from is not None]
    failed = [e for e in entries if e.status != "ok"]

    baselines = {p.stem for p in BASELINES_DIR.glob("*.json")}
    unchecked = sorted(set(state.policies) - baselines)

    key_rows = keys.list_for_console()
    accounts = _accounts().list_accounts()
    frozen = [a for a in accounts if a["state"] == "locked"]
    failing = [
        a for a in accounts if a["state"] != "locked" and a["failed_attempts"] > 0
    ]
    orphans = orphan_overrides()
    pending = [
        r for r in _requests().list_requests(state.declared) if r["state"] == "pending"
    ]

    by_tenant: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    for e in entries:
        t = by_tenant.setdefault(e.tenant, {"name": e.tenant, "calls": 0, "spend": 0})
        t["calls"] += 1
        t["spend"] += e.cost_nanousd
        m = by_model.setdefault(e.model, {"name": e.model, "calls": 0, "spend": 0})
        m["calls"] += 1
        m["spend"] += e.cost_nanousd

    def top(d: dict, by: str) -> list[dict]:
        # Tenants rank by spend and models by call count, because those are
        # the questions each answers: "who is costing us money" and "what is
        # this gateway actually serving".
        return sorted(d.values(), key=lambda r: -r[by])[:5]

    # `count: None` means "nothing to judge", which is not the same as zero.
    # A panel that renders an unmeasured thing as a green zero is the failure
    # the three-state gate matrix exists to avoid.
    no_ledger = not entries
    alerts = [
        {
            "key": "over_budget", "level": "bad", "view": "tenants",
            "title": "业务线已用满日额度", "window": "today",
            "count": None if no_ledger else len(over), "items": over,
            "detail": "这些业务线的请求正在被拒绝。",
        },
        {
            "key": "zero_budget", "level": "warn", "view": "tenants",
            "title": "日额度为 0 的业务线", "window": "now",
            "count": len(zero_budget), "items": zero_budget,
            "detail": "额度 0 表示关停，不表示不限量——它们一个请求也发不出去。",
        },
        {
            "key": "vetoed", "level": "warn", "view": "perms",
            "title": "被否决的模型替换", "window": "since_boot",
            "count": len(vetoed),
            "items": [
                {"tenant": e.tenant, "requested": e.requested, "routed": e.routed,
                 "reason": e.reason, "ts": e.ts.isoformat()}
                for e in vetoed[-20:]
            ],
            "detail": "路由想换的模型被多样性红线拦下了。",
        },
        {
            "key": "fallbacks", "level": "warn", "view": "tenants",
            "title": "发生过降级的调用", "window": "today",
            "count": None if no_ledger else len(fallbacks),
            "items": [
                {"tenant": e.tenant, "from": e.fallback_from, "to": e.model,
                 "ts": _aware(e.ts).isoformat()}
                for e in fallbacks[-20:]
            ],
            "detail": "降级已按红线要求留痕，不是静默发生的。",
        },
        {
            "key": "failed", "level": "bad", "view": "tenants",
            "title": "失败或中断的调用", "window": "today",
            "count": None if no_ledger else len(failed),
            "items": [], "of": len(entries),
            "detail": "占今日全部调用的比例见括号内。",
        },
        {
            "key": "orphans", "level": "warn", "view": "perms",
            "title": "已失效的管控", "window": "now",
            "count": len(orphans),
            "items": [
                {"id": o["id"], "tenant": o["tenant"], "field": o["field"],
                 "why": o["why"]}
                for o in orphans
            ],
            "detail": "策略文件变过，这些收回不再起作用，但仍显示为生效中。",
        },
        {
            "key": "unchecked", "level": "warn", "view": "tenants",
            "title": "未覆盖质量门禁的业务线", "window": "now",
            "count": len(unchecked), "items": unchecked,
            "detail": "没人检查过，不等于检查过没问题。",
        },
        {
            "key": "pending", "level": "info", "view": "changes",
            "title": "待发布的变更申请", "window": "now",
            "count": len(pending),
            "items": [
                {"id": r["id"], "tenant": r["tenant"], "kind": r["kind"],
                 "field": r["field"], "requested_by": r["requested_by"]}
                for r in pending
            ],
            "detail": "已提交但还没落进策略文件。",
        },
        {
            "key": "accounts", "level": "warn", "view": "admins",
            "title": "被冻结或登录失败的账号", "window": "now",
            "count": len(frozen) + len(failing),
            "items": [
                {"username": a["username"], "state": a["state"],
                 "failed_attempts": a["failed_attempts"]}
                for a in frozen + failing
            ],
            "detail": "连续失败 5 次会冻结 15 分钟。",
        },
    ]

    return {
        # Stated on the front page and not foldable. A control plane pointed
        # at the deterministic fake upstream looks exactly like one pointed at
        # real providers -- same panels, same confident totals, and every
        # number is invented. Somebody raising a budget off these needs to
        # know which one they are looking at without going to find out.
        "upstream": settings.upstream,
        "window": {"today_since": since.isoformat(), "now": now.isoformat()},
        "totals": {
            "calls_today": len(entries),
            "spend_today_nanousd": sum(spend.values()),
            "budget_total_nanousd": sum(
                p.budget_nanousd_per_day for p in state.policies.values()
            ),
            "tenants_online": len(online),
            "tenants_off": len(switched_off),
            "tenants_over_budget": len(over),
            "keys_active": len([k for k in key_rows if k["state"] == "active"]),
            "keys_total": len(key_rows),
            "has_ledger_evidence": not no_ledger,
        },
        "alerts": alerts,
        "top_tenants": top(by_tenant, "spend"),
        "top_models": top(by_model, "calls"),
        "recent_actions": cp.recent_actions(limit=6),
    }


# --- page ----------------------------------------------------------------


@router.get("/admin/static/{name}")
def admin_asset(name: str) -> FileResponse:
    """The page's stylesheet and scripts.

    Anonymous for the same reason the shell is: they are markup and logic,
    not data. Every panel they draw is fetched from an endpoint that requires
    a session, so an unauthenticated fetch of these gets an empty console.

    `name` is looked up in a whitelist rather than joined onto a path. Joining
    is how a static route becomes a way to read `../../../.env`.
    """
    media_type = STATIC_FILES.get(name)
    if media_type is None:
        raise HTTPException(status_code=404, detail="没有这个静态资源")
    return FileResponse(STATIC_DIR / name, media_type=media_type)


@router.get("/admin")
def admin_page() -> FileResponse:
    """The page shell, unauthenticated -- because it *is* the login form.

    Serving this anonymously is not the leak it would have been under the old
    scheme. It carries no data: every panel it fetches requires a session, and
    a page that renders nothing without one gives away nothing but its own
    existence. The credential no longer travels in the URL at all.
    """
    return FileResponse(Path(__file__).resolve().parent / "static" / "admin.html")

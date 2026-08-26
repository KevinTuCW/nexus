"""Usage read-out, with the one boundary crossing this platform permits.

A tenant may always read its own usage. Reading anyone else's requires an
explicit `cross_tenant_read` grant in that tenant's policy, and the default
is no grant at all.

**Partial authorisation refuses the whole request.** Returning the
authorised subset would tell the caller that the unauthorised tenant spent
nothing — and a group-wide digest quietly missing a business line is worse
than an error, because the error gets fixed and the digest gets taken into a
meeting.

Own-tenant reads are not audited as crossings. Every tenant reads its own
usage constantly; logging that would bury the handful of real crossings in
noise, which is the same as not logging them.
"""

from fastapi import APIRouter, Header, HTTPException, Query

from nexus.ingress.auth import AuthError, authenticate
from nexus.ledger.book import rollup
from nexus.state import get_state

router = APIRouter()


@router.get("/v1/usage")
def usage(
    authorization: str = Header(default=""),
    tenants: str | None = Query(default=None),
) -> dict:
    state = get_state()
    try:
        caller = authenticate(authorization, state.key_index)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    requested = tuple(t.strip() for t in tenants.split(",")) if tenants else (caller,)
    crossings = tuple(t for t in requested if t != caller)

    if crossings:
        policy = state.policies[caller]
        ungranted = [t for t in crossings if t not in policy.cross_tenant_read]
        if ungranted:
            state.audit.record_cross_tenant_denial(caller, crossings)
            raise HTTPException(
                status_code=403,
                detail=(
                    f"tenant '{caller}' has no cross_tenant_read grant for "
                    f"{ungranted}. Refusing the whole request rather than "
                    "returning a partial answer: a partial answer reads as "
                    "'they spent nothing'."
                ),
            )
        state.audit.record_cross_tenant_read(caller, crossings)

    wanted = set(requested)
    rows = [row for row in state.ledger.entries() if row.tenant in wanted]
    by_tenant: dict[str, int] = {}
    for row in rows:
        by_tenant[row.tenant] = by_tenant.get(row.tenant, 0) + row.cost_nanousd
    return {
        "by_tenant": by_tenant,
        "by_workload": rollup(rows),
        "currency_unit": "nanousd",
        "n_rows": len(rows),
    }

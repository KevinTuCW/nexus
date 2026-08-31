"""The FinOps console's read side.

Every panel authenticates **and authorises**. The first of those was in place
from the start; the second was not, and the gap was the sharpest thing in the
repository: `/v1/usage` refused helpmate a look at wealthwise's spend with a
paragraph of reasoning, and `/console/costs` handed over the same numbers to
the same key on the next line. Authentication answers "who is calling"; only
authorisation answers "and may they see this".

The scoping rule is not defined here. It is `ingress.authz.visible_tenants`,
the same module `/v1/usage` calls, because an authorisation rule with two
implementations has two meanings and the looser one wins.

wuwork still sees everything, and that is the point rather than an exception:
its policy grants `cross_tenant_read` over all four incumbents so group
finance can write the operations digest. Scoping is not a lockout — it is the
console telling the truth about which of its readers were authorised for
what.

The gate matrix has three states, not two. `not_covered` is what a tenant
with no baseline gets, and it is deliberately not `pass`: aura emits no
metrics and helpmate's gate exceeds a conformance budget, so for both of
them the honest answer is "nobody checked", and a green cell would say
something else.
"""

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Header
from fastapi.responses import FileResponse

from nexus.ingress.authz import visible_tenants
from nexus.ledger.book import rollup
from nexus.policy.quota import day_start
from nexus.state import get_state
from nexus.ingress.caller import resolve_caller

BASELINES_DIR = Path(__file__).resolve().parents[3] / "baselines"

router = APIRouter()


def _scope(authorization: str) -> frozenset[str]:
    """Authenticate the caller and return the tenants it may see."""
    caller = resolve_caller(authorization)
    state = get_state()
    return visible_tenants(caller, state.policies, state.audit)


@router.get("/console/costs")
def costs(authorization: str = Header(default="")) -> dict:
    scope = _scope(authorization)
    rows = [r for r in get_state().ledger.entries() if r.tenant in scope]
    by_tenant: dict[str, int] = {}
    for row in rows:
        by_tenant[row.tenant] = by_tenant.get(row.tenant, 0) + row.cost_nanousd
    return {
        "by_tenant": by_tenant,
        "by_workload": rollup(rows),
        # Named, not implied. A number rendered without its unit is a number
        # someone will read as dollars.
        "currency_unit": "nanousd",
        "n_rows": len(rows),
        # Stated on every panel: a view that silently covers one tenant looks
        # exactly like a group that spent nothing on the other four.
        "scope": sorted(scope),
    }


@router.get("/console/routing")
def routing(authorization: str = Header(default="")) -> dict:
    scope = _scope(authorization)
    return {
        "events": [
            {
                "tenant": e.tenant,
                "requested": e.requested,
                "routed": e.routed,
                "vetoed": e.vetoed,
                "reason": e.reason,
                "ts": e.ts.isoformat(),
            }
            for e in get_state().routing.events()
            if e.tenant in scope
        ],
        "scope": sorted(scope),
    }


@router.get("/console/gates")
def gates(authorization: str = Header(default="")) -> dict:
    scope = _scope(authorization)
    state = get_state()
    baselines = {p.stem for p in BASELINES_DIR.glob("*.json")}
    rows = []
    for name in sorted(state.policies):
        if name not in scope:
            continue
        rows.append(
            {
                "tenant": name,
                "gate_command": state.policies[name].gate_command,
                # Three states. "not_covered" means nobody checked, which is
                # not the same as "checked and fine".
                "state": "pass" if name in baselines else "not_covered",
                "has_baseline": name in baselines,
            }
        )
    return {"tenants": rows, "scope": sorted(scope)}


@router.get("/console/fallbacks")
def fallbacks(authorization: str = Header(default="")) -> dict:
    scope = _scope(authorization)
    return {
        "events": [
            {
                "call_id": r.call_id,
                "tenant": r.tenant,
                "from": r.fallback_from,
                "to": r.model,
                "ts": r.ts.isoformat(),
            }
            for r in get_state().ledger.entries()
            if r.fallback_from is not None and r.tenant in scope
        ],
        "scope": sorted(scope),
    }


@router.get("/console/quota")
def quota(authorization: str = Header(default="")) -> dict:
    """Budget against **today's** spend, on the same clock the gateway uses.

    This panel used to sum the whole ledger and compare the total against a
    field named `budget_nanousd_per_day`. On day one those agree; by day
    thirty the panel is reporting a month against a daily allowance, and a
    tenant comfortably inside its budget renders as one running at 30x.
    Reading `spent_since` from the same `day_start` the request path enforces
    on is what keeps this panel and the 429 telling one story.
    """
    scope = _scope(authorization)
    state = get_state()
    since = day_start(datetime.now(timezone.utc))
    rows = []
    for name, policy in sorted(state.policies.items()):
        if name not in scope:
            continue
        budget = policy.budget_nanousd_per_day
        spent = state.ledger.spent_since(name, since)
        rows.append(
            {
                "tenant": name,
                "budget_nanousd_per_day": budget,
                "spent_nanousd": spent,
                # Three states, for the same reason the gate matrix has
                # three. "switched_off" is a budget of 0, which means off and
                # never unlimited; "over_budget" is a tenant being refused
                # right now, and it has to be visible here rather than only
                # in a 429 body that some SDK swallowed.
                "state": (
                    "switched_off"
                    if budget == 0
                    else "over_budget"
                    if spent >= budget
                    else "active"
                ),
                "window_start": since.isoformat(),
            }
        )
    return {"tenants": rows, "currency_unit": "nanousd", "scope": sorted(scope)}


@router.get("/console/mode")
def mode(authorization: str = Header(default="")) -> dict:
    """Which upstream this gateway is actually talking to.

    A console pointed at the fake upstream looks exactly like one pointed at
    real providers: same panels, same numbers, same confident totals. The
    banner this feeds exists so nobody reads a demo as production.
    """
    from nexus.config import get_settings

    _scope(authorization)
    return {"upstream": get_settings().upstream}


@router.get("/console")
def console_page() -> FileResponse:
    """The page shell, unauthenticated; every panel it fetches is not.

    Keeping auth on the data endpoints rather than the shell puts the whole
    access-control story in one place. A page that renders nothing without a
    key is not a leak.
    """
    return FileResponse(Path(__file__).resolve().parent / "static" / "console.html")

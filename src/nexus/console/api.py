"""The FinOps console's read side.

Every panel authenticates. The console reads every tenant's data, so an
unauthenticated panel is a cross-tenant leak with a nicer font.

The gate matrix has three states, not two. `not_covered` is what a tenant
with no baseline gets, and it is deliberately not `pass`: aura emits no
metrics and helpmate's gate exceeds a conformance budget, so for both of
them the honest answer is "nobody checked", and a green cell would say
something else.
"""

from pathlib import Path

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import FileResponse

from nexus.ingress.auth import AuthError, authenticate
from nexus.ledger.book import rollup
from nexus.state import get_state

BASELINES_DIR = Path(__file__).resolve().parents[3] / "baselines"

router = APIRouter()


def _require_auth(authorization: str) -> str:
    state = get_state()
    try:
        return authenticate(authorization, state.key_index)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.get("/console/costs")
def costs(authorization: str = Header(default="")) -> dict:
    _require_auth(authorization)
    rows = get_state().ledger.entries()
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
    }


@router.get("/console/routing")
def routing(authorization: str = Header(default="")) -> dict:
    _require_auth(authorization)
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
        ]
    }


@router.get("/console/gates")
def gates(authorization: str = Header(default="")) -> dict:
    _require_auth(authorization)
    state = get_state()
    baselines = {p.stem for p in BASELINES_DIR.glob("*.json")}
    rows = []
    for name in sorted(state.policies):
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
    return {"tenants": rows}


@router.get("/console/fallbacks")
def fallbacks(authorization: str = Header(default="")) -> dict:
    _require_auth(authorization)
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
            if r.fallback_from is not None
        ]
    }


@router.get("/console/quota")
def quota(authorization: str = Header(default="")) -> dict:
    _require_auth(authorization)
    state = get_state()
    spent: dict[str, int] = {}
    for row in state.ledger.entries():
        spent[row.tenant] = spent.get(row.tenant, 0) + row.cost_nanousd
    rows = []
    for name, policy in sorted(state.policies.items()):
        budget = policy.budget_nanousd_per_day
        rows.append(
            {
                "tenant": name,
                "budget_nanousd_per_day": budget,
                "spent_nanousd": spent.get(name, 0),
                # Budget 0 means switched off, never unlimited. The panel
                # says so in words so nobody has to remember the convention.
                "state": "switched_off" if budget == 0 else "active",
            }
        )
    return {"tenants": rows, "currency_unit": "nanousd"}


@router.get("/console/mode")
def mode(authorization: str = Header(default="")) -> dict:
    """Which upstream this gateway is actually talking to.

    A console pointed at the fake upstream looks exactly like one pointed at
    real providers: same panels, same numbers, same confident totals. The
    banner this feeds exists so nobody reads a demo as production.
    """
    from nexus.config import get_settings

    _require_auth(authorization)
    return {"upstream": get_settings().upstream}


@router.get("/console")
def console_page() -> FileResponse:
    """The page shell, unauthenticated; every panel it fetches is not.

    Keeping auth on the data endpoints rather than the shell puts the whole
    access-control story in one place. A page that renders nothing without a
    key is not a leak.
    """
    return FileResponse(Path(__file__).resolve().parent / "static" / "console.html")

"""Who is calling, and may they call at all. One function, five surfaces.

`enabled` arrived with the effective-policy layer as a field nothing
enforced. A control written, tested and drawn into the architecture diagram
but wired to no real path is the exact shape of all four findings from this
repository's last architecture review, and shipping a fifth would be hard to
excuse having just written that sentence down.

The enforcement point is a function rather than a check repeated at each
entry. There are five of them -- chat completions, two passthrough routes,
`/v1/usage` and the console -- and a rule copied five times is five rules
that will disagree the first time one of them is edited. That is not
hypothetical here: the authorisation rule *was* copied, was real in
`/v1/usage`, and was absent in the console.

Disabling a tenant makes its credential inert; it does not erase its
history. The console lists tenants from the policy registry rather than from
who is currently enabled, so group finance still sees what a switched-off
business line spent last month.
"""

from fastapi import HTTPException

from nexus.ingress.auth import AuthError, authenticate
from nexus.state import get_state


def resolve_caller(authorization: str) -> str:
    """Authenticate a tenant credential and refuse a disabled tenant.

    Raises `HTTPException` directly: every caller did the same conversion,
    and doing it here is what keeps the five surfaces from drifting apart in
    which status code they return for which failure.
    """
    state = get_state()
    try:
        tenant = authenticate(authorization, state.key_index)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    policy = state.policies.get(tenant)
    if policy is not None and not policy.enabled:
        # 403, not 401. The credential is valid and was recognised; the
        # tenant it names is switched off. Answering 401 would send an
        # operator hunting for a key problem that does not exist.
        raise HTTPException(
            status_code=403,
            detail=(
                f"tenant '{tenant}' is switched off. The credential is valid; "
                "the tenant is not accepting traffic."
            ),
        )
    return tenant

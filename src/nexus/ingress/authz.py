"""Which tenants a caller may see. One definition, two surfaces.

The rule itself is short and was never in doubt: a tenant reads its own
usage, and reads anyone else's only under an explicit `cross_tenant_read`
grant. What went wrong is that it was implemented in `/v1/usage` and nowhere
else, while the console — five panels over the same ledger — asked only
whether the caller was *a* tenant. Any tenant key returned every tenant's
spend, routing history, fallbacks and budget.

That is the whole failure mode of an internal platform in one endpoint: the
boundary is real on the path people examine and absent on the path people
use. So the rule lives here, both surfaces call it, and a third surface
cannot quietly grow a fourth interpretation.

Two shapes, because the surfaces ask different questions:

  - `authorize_explicit` — the caller *named* the tenants it wants
    (`/v1/usage?tenants=…`). Naming an ungranted tenant is refused outright,
    whole request, no partial answer: a subset reads as "they spent nothing",
    and a group digest quietly missing a business line still gets taken into
    a meeting.
  - `visible_tenants` — the caller named nobody and gets a view
    (the console). Here the honest response to an ungranted tenant is to
    leave it out, not to refuse: there is no request to be partial about, and
    a console that 403s because some other business line exists is a console
    nobody opens.

Both audit the crossing, and only the crossing. Own-tenant reads are not
audited: every tenant reads itself constantly, and burying a handful of real
crossings in that noise is the same as not recording them.
"""

from nexus.audit import InMemoryAudit
from nexus.registry.tenants import TenantPolicy


class CrossTenantDenied(Exception):
    """The caller named a tenant it holds no grant for."""

    def __init__(self, caller: str, ungranted: list[str]) -> None:
        self.caller = caller
        self.ungranted = ungranted
        super().__init__(
            f"tenant '{caller}' has no cross_tenant_read grant for "
            f"{ungranted}. Refusing the whole request rather than returning "
            "a partial answer: a partial answer reads as 'they spent "
            "nothing'."
        )


def authorize_explicit(
    caller: str,
    requested: tuple[str, ...],
    policies: dict[str, TenantPolicy],
    audit: InMemoryAudit,
) -> tuple[str, ...]:
    """Check a caller's named tenant list, or raise `CrossTenantDenied`."""
    crossings = tuple(t for t in requested if t != caller)
    if not crossings:
        return requested

    granted = policies[caller].cross_tenant_read
    ungranted = [t for t in crossings if t not in granted]
    if ungranted:
        audit.record_cross_tenant_denial(caller, crossings)
        raise CrossTenantDenied(caller, ungranted)
    audit.record_cross_tenant_read(caller, crossings)
    return requested


def visible_tenants(
    caller: str, policies: dict[str, TenantPolicy], audit: InMemoryAudit
) -> frozenset[str]:
    """The tenant names a caller's console view may contain.

    Grants naming a tenant that no longer has a policy are kept in the set
    rather than dropped. They match nothing, so they widen no view; dropping
    them here would put a second opinion about which tenants exist in a
    module whose job is not to have one.
    """
    policy = policies[caller]
    crossings = tuple(t for t in policy.cross_tenant_read if t != caller)
    if crossings:
        audit.record_cross_tenant_read(caller, crossings)
    return frozenset({caller, *policy.cross_tenant_read})

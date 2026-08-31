"""Process-wide wiring, reachable without importing the application object.

Phase 1 kept `get_state` in `app.py`. Because `app.py` imports the router
from `ingress.api`, the handler could not import `get_state` at module level
and did it inside the function instead. That workaround was correct and
unscalable: routing, diversity and fallback all need state, and each would
have repeated it.

Putting state here inverts the dependency — `app.py` and every router import
`state`, and nothing imports `app`. `tests/test_state.py` asserts that
structurally, so the cycle cannot come back quietly.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import lru_cache

from nexus.audit import InMemoryAudit
from nexus.config import get_settings
from nexus.ingress.auth import build_key_index
from nexus.ledger.book import InMemoryLedger, Ledger
from nexus.policy.diversity import GroupLedger
from nexus.registry.effective import Override, compose, load_effective_policies
from nexus.registry.tenants import TenantPolicy, load_policies
from nexus.routing_log import RoutingLog
from nexus.upstream import FakeUpstream, Upstream


@dataclass
class State:
    policies: dict[str, TenantPolicy]
    key_index: dict[str, str]
    ledger: Ledger
    upstream: Upstream
    #: Per-group weight-family reservations, for native tenants only.
    groups: GroupLedger
    audit: InMemoryAudit
    routing: RoutingLog
    #: The YAML as written, before any override. Kept alongside the effective
    #: policies for two reasons: recomposing after a control-plane change
    #: needs the declared value to narrow from, and the console has to show
    #: both. Displaying only the effective value cannot answer "was this
    #: always so, or did somebody tighten it"; displaying only the declared
    #: value is a screen that lies.
    declared: dict[str, TenantPolicy] = field(default_factory=dict)

    def recompose(
        self,
        tenant: str,
        overrides: "Sequence[Override]",
        budget: int | None,
    ) -> None:
        """Swap one tenant's effective policy in place.

        In place, and never `get_state.cache_clear()`. Clearing rebuilds the
        whole `State`, which discards `RoutingLog` -- the record of what G1
        vetoed, and the only reason the console's routing panel has anything
        to show. A control plane that erased the audit surface every time
        somebody used it would be worse than none.
        """
        self.policies[tenant] = compose(self.declared[tenant], overrides, budget)


@lru_cache(maxsize=1)
def get_state() -> State:
    """Cached so the ledger survives across requests.

    Tests call `get_state.cache_clear()` to start from an empty book.
    """
    settings = get_settings()

    # Overrides and budgets come from the control plane when there is one.
    # No database means neither exists -- not an error, the same fallback the
    # ledger makes, so a fresh clone still runs offline with nothing but YAML.
    overrides: list[Override] = []
    budgets: dict[str, int] = {}
    if settings.database_url:
        from nexus.admin.store import ControlPlaneStore

        cp = ControlPlaneStore(settings.database_url)
        overrides = cp.active_overrides()
        budgets = cp.current_budgets()

    # Effective, not declared. Control-plane overrides are merged here, so
    # every layer below -- routing, the diversity guard, quota, the console
    # -- reads the effective policy without each having to remember to look
    # an override table up.
    policies = load_effective_policies(settings.policies_dir, overrides, budgets)
    declared = load_policies(settings.policies_dir)

    upstream: Upstream
    if settings.upstream == "fake":
        upstream = FakeUpstream()
    elif settings.upstream == "litellm":
        # Imported here rather than at module level. Measured, not assumed:
        # this alone is *not* what keeps `import nexus.app` free of LiteLLM
        # -- `upstream_litellm` also imports the SDK lazily, inside its
        # transport functions, and either layer suffices on its own.
        # Breaking one leaves `tests/test_no_network.py` green; only
        # breaking both turns it red. Two layers is deliberate: the import
        # here keeps a fake-upstream process from paying LiteLLM's import
        # cost (~10s), and the one over there keeps that true even if this
        # module is later rewritten.
        from nexus.upstream_litellm import LiteLLMUpstream

        upstream = LiteLLMUpstream(timeout_s=settings.upstream_timeout_s)
    else:
        raise ValueError(
            f"unknown UPSTREAM '{settings.upstream}'; expected 'fake' or 'litellm'"
        )

    ledger: Ledger
    if settings.database_url:
        # Imported here so psycopg is not a hard import dependency on a
        # machine without Postgres.
        from nexus.ledger.pg import PgLedger

        ledger = PgLedger(settings.database_url)
    else:
        ledger = InMemoryLedger()

    # No database means no issued credentials -- not an error. A fresh clone
    # still runs its whole test suite with nothing but the bootstrap keys in
    # the environment, which is the same reason the ledger falls back to
    # memory rather than refusing to start.
    stored_digests: dict[str, str] = {}
    if settings.database_url:
        from nexus.admin.store import TenantKeyStore

        stored_digests = TenantKeyStore(settings.database_url).active_digests()

    key_index = build_key_index(policies, stored=stored_digests)

    return State(
        policies=policies,
        key_index=key_index,
        ledger=ledger,
        upstream=upstream,
        groups=GroupLedger(),
        audit=InMemoryAudit(),
        routing=RoutingLog(),
        declared=declared,
    )

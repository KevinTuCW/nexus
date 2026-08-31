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

from dataclasses import dataclass
from functools import lru_cache

from nexus.audit import InMemoryAudit
from nexus.config import get_settings
from nexus.ingress.admin_auth import Admin, load_admin_index
from nexus.ingress.auth import build_key_index
from nexus.ledger.book import InMemoryLedger, Ledger
from nexus.policy.diversity import GroupLedger
from nexus.registry.effective import load_effective_policies
from nexus.registry.tenants import TenantPolicy
from nexus.routing_log import RoutingLog
from nexus.upstream import FakeUpstream, Upstream


@dataclass
class State:
    policies: dict[str, TenantPolicy]
    key_index: dict[str, str]
    #: Control-plane identities, a separate namespace from `key_index`. Empty
    #: means no administrators, which means `/admin` is not mounted at all.
    admin_index: dict[str, Admin]
    ledger: Ledger
    upstream: Upstream
    #: Per-group weight-family reservations, for native tenants only.
    groups: GroupLedger
    audit: InMemoryAudit
    routing: RoutingLog


@lru_cache(maxsize=1)
def get_state() -> State:
    """Cached so the ledger survives across requests.

    Tests call `get_state.cache_clear()` to start from an empty book.
    """
    settings = get_settings()
    # Effective, not declared. Control-plane overrides are merged here, so
    # every layer below -- routing, the diversity guard, quota, the console
    # -- reads the effective policy without each having to remember to look
    # an override table up.
    policies = load_effective_policies(settings.policies_dir)

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
        # Built against the tenant index so a credential serving both roles
        # is refused here, at startup, rather than discovered later as a
        # tenant key that happened to work on the control plane.
        admin_index=load_admin_index(key_index),
        ledger=ledger,
        upstream=upstream,
        groups=GroupLedger(),
        audit=InMemoryAudit(),
        routing=RoutingLog(),
    )

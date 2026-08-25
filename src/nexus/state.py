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

from nexus.config import get_settings
from nexus.ingress.auth import build_key_index
from nexus.ledger.book import InMemoryLedger
from nexus.registry.tenants import TenantPolicy, load_policies
from nexus.upstream import FakeUpstream, Upstream


@dataclass
class State:
    policies: dict[str, TenantPolicy]
    key_index: dict[str, str]
    ledger: InMemoryLedger
    upstream: Upstream


@lru_cache(maxsize=1)
def get_state() -> State:
    """Cached so the ledger survives across requests.

    Tests call `get_state.cache_clear()` to start from an empty book.
    """
    settings = get_settings()
    policies = load_policies(settings.policies_dir)

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

    return State(
        policies=policies,
        key_index=build_key_index(policies),
        ledger=InMemoryLedger(),
        upstream=upstream,
    )

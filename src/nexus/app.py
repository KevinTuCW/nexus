"""nexus application wiring."""

from dataclasses import dataclass
from functools import lru_cache

from fastapi import FastAPI

from nexus.config import get_settings
from nexus.ingress.api import router
from nexus.ingress.auth import build_key_index
from nexus.ledger.book import InMemoryLedger
from nexus.registry.tenants import TenantPolicy, load_policies
from nexus.upstream import FakeUpstream


@dataclass
class State:
    policies: dict[str, TenantPolicy]
    key_index: dict[str, str]
    ledger: InMemoryLedger
    upstream: FakeUpstream


@lru_cache(maxsize=1)
def get_state() -> State:
    """Process-wide state.

    Cached so the ledger survives across requests; tests call
    `get_state.cache_clear()` to start from an empty book.
    """
    settings = get_settings()
    policies = load_policies(settings.policies_dir)
    return State(
        policies=policies,
        key_index=build_key_index(policies),
        ledger=InMemoryLedger(),
        upstream=FakeUpstream(),
    )


app = FastAPI(title="nexus", version="0.1.0")
app.include_router(router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

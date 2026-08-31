"""Tenant authentication: one API key per tenant, read from the environment.

Zero-touch tenants send nothing but their key — their repos are unmodified,
so the key is the *only* attribution signal nexus gets. That makes two
things load-bearing:

  - an unset key must never become a blank key. A blank entry in the index
    would make a tenant reachable by sending nothing at all;
  - two tenants must never share a key. Attribution would be ambiguous and
    the ledger would confidently bill one for the other's traffic.

Both are refused at index-build time, not at request time, so the failure
shows up at startup rather than in a month of mis-attributed invoices.

The index is keyed by SHA-256 digest, not plaintext. Before Phase 4a this
process held every tenant's key in memory in the clear for its whole
lifetime, which a heap dump, a crash report or a debugger would have handed
over in one step. Hashing costs nothing here -- the index is built once and
a lookup hashes one short string -- and `hmac.compare_digest` is kept so the
comparison stays constant-time.

Credentials come from two sources: the environment (one bootstrap key per
tenant, the only mechanism before Phase 4a) and the `tenant_key` table
written by the control plane. They share one index and one duplicate check,
because a key colliding across the two sources is exactly as ambiguous as
one colliding within either.
"""

import hashlib
import hmac
import os

from nexus.registry.tenants import TenantPolicy


class AuthError(Exception):
    """Presented credential does not identify a tenant."""


def key_digest(presented: str) -> str:
    """One hashing rule, shared by the index and the credential store.

    Two implementations of this would mean a key issued by the control plane
    hashes to something the index never matches, and the symptom would be a
    401 for a credential that exists and is correct.
    """
    return hashlib.sha256(presented.strip().encode("utf-8")).hexdigest()


def build_key_index(
    policies: dict[str, TenantPolicy],
    stored: dict[str, str] | None = None,
) -> dict[str, str]:
    """Map sha256(API key) -> tenant name.

    Tenants whose key env var is unset or blank are simply absent, which
    means unreachable. `stored` carries digests already issued by the control
    plane, keyed the same way; it is empty when there is no database, so a
    fresh clone still needs no Postgres to run its tests.
    """
    index: dict[str, str] = {}

    def put(digest: str, tenant: str, origin: str) -> None:
        # Same tenant from both sources is not a collision: a bootstrap key
        # and its stored twin name one tenant, so attribution is unambiguous.
        if digest in index and index[digest] != tenant:
            raise ValueError(
                f"tenants '{index[digest]}' and '{tenant}' share an API key "
                f"({origin}); attribution would be ambiguous and the ledger "
                "would bill one for the other's traffic"
            )
        index[digest] = tenant

    for name, policy in policies.items():
        key = os.environ.get(policy.api_key_env, "").strip()
        if not key:
            continue
        put(key_digest(key), name, "environment")

    for digest, tenant in (stored or {}).items():
        put(digest, tenant, "tenant_key")

    return index


def authenticate(authorization: str, index: dict[str, str]) -> str:
    """Resolve an Authorization header value to a tenant name."""
    presented = authorization.removeprefix("Bearer ").strip()
    if not presented:
        raise AuthError("missing credential")
    presented_digest = key_digest(presented)
    for known, tenant in index.items():
        if hmac.compare_digest(known, presented_digest):
            return tenant
    raise AuthError("unknown credential")

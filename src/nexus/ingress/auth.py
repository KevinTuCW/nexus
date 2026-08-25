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
"""

import hmac
import os

from nexus.registry.tenants import TenantPolicy


class AuthError(Exception):
    """Presented credential does not identify a tenant."""


def build_key_index(policies: dict[str, TenantPolicy]) -> dict[str, str]:
    """Map configured API key -> tenant name.

    Tenants whose key env var is unset or blank are simply absent, which
    means unreachable.
    """
    index: dict[str, str] = {}
    for name, policy in policies.items():
        key = os.environ.get(policy.api_key_env, "").strip()
        if not key:
            continue
        if key in index:
            raise ValueError(
                f"tenants '{index[key]}' and '{name}' share an API key; "
                "attribution would be ambiguous and the ledger would bill "
                "one for the other's traffic"
            )
        index[key] = name
    return index


def authenticate(authorization: str, index: dict[str, str]) -> str:
    """Resolve an Authorization header value to a tenant name."""
    presented = authorization.removeprefix("Bearer ").strip()
    if not presented:
        raise AuthError("missing credential")
    for key, tenant in index.items():
        if hmac.compare_digest(key, presented):
            return tenant
    raise AuthError("unknown credential")

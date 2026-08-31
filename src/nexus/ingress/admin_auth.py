"""Who may enter the control plane. A different namespace from `key_index`.

`key_index` maps a credential to a *tenant name*, and an administrator is not
a tenant. Reusing it would mean a data-plane credential could carry
administrative power, and this repository has already learned once what
happens when authentication is mistaken for authorisation: the console
authenticated every caller and authorised none of them, so any tenant key
returned every tenant's spend.

So the two indexes are separate, and the separation is checked rather than
trusted: an administrator digest that also appears in the tenant index
refuses to start, the same way two tenants sharing a key already does.

Administrators are named, not shared. A single NEXUS_ADMIN_KEY would make
every row in `admin_action` say "admin", and an audit trail that cannot name
a person answers no question worth asking -- which is the whole reason the
control plane keeps one.
"""

import hmac
import os

from nexus.ingress.auth import key_digest

#: `name:key` pairs, comma separated. A `:ro` suffix on the name marks a
#: read-only administrator:
#:
#:     NEXUS_ADMIN_KEYS="kevin:nx-admin-aaa,ops-li:ro:nx-admin-bbb"
ADMIN_KEYS_ENV = "NEXUS_ADMIN_KEYS"


class AdminAuthError(Exception):
    """Presented credential does not identify an administrator."""


class Admin:
    """A named administrator and what they are allowed to do."""

    __slots__ = ("name", "role")

    def __init__(self, name: str, role: str) -> None:
        self.name = name
        self.role = role

    @property
    def may_write(self) -> bool:
        return self.role == "rw"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Admin(name={self.name!r}, role={self.role!r})"


def build_admin_index(
    raw: str, tenant_index: dict[str, str] | None = None
) -> dict[str, Admin]:
    """Parse `NEXUS_ADMIN_KEYS` into `sha256 -> Admin`.

    Blank input yields an empty index, which means the control plane has no
    administrators and therefore is not mounted at all. That is the same
    failure direction as a blank tenant key building no index: absent, not
    permissive.
    """
    index: dict[str, Admin] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":")
        if len(parts) == 2:
            name, key = parts[0].strip(), parts[1].strip()
            role = "rw"
        elif len(parts) == 3 and parts[1].strip() == "ro":
            name, key = parts[0].strip(), parts[2].strip()
            role = "ro"
        else:
            raise ValueError(
                f"malformed {ADMIN_KEYS_ENV} entry {entry!r}; expected "
                "'name:key' or 'name:ro:key'"
            )
        if not name or not key:
            raise ValueError(
                f"malformed {ADMIN_KEYS_ENV} entry {entry!r}; neither the "
                "name nor the key may be blank"
            )

        digest = key_digest(key)
        if digest in index:
            raise ValueError(
                f"administrators '{index[digest].name}' and '{name}' share a "
                "key; an audit trail that cannot tell them apart is not one"
            )
        if tenant_index and digest in tenant_index:
            raise ValueError(
                f"administrator '{name}' shares a key with tenant "
                f"'{tenant_index[digest]}'; a data-plane credential must "
                "never carry control-plane power"
            )
        index[digest] = Admin(name=name, role=role)
    return index


def load_admin_index(tenant_index: dict[str, str] | None = None) -> dict[str, Admin]:
    """Build the administrator index from the environment."""
    return build_admin_index(os.environ.get(ADMIN_KEYS_ENV, ""), tenant_index)


def authenticate_admin(authorization: str, index: dict[str, Admin]) -> Admin:
    """Resolve an Authorization header to a named administrator."""
    presented = authorization.removeprefix("Bearer ").strip()
    if not presented:
        raise AdminAuthError("missing credential")
    presented_digest = key_digest(presented)
    for known, admin in index.items():
        if hmac.compare_digest(known, presented_digest):
            return admin
    raise AdminAuthError("unknown credential")

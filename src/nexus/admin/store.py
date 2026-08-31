"""Reads and writes for the control plane's credential table.

Separate from `ledger/pg.py` because the two answer different questions and
change for different reasons: that module records what was spent, this one
records who was allowed to spend it.

Plaintext keys exist in exactly one place -- the return value of
`issue_key()` -- and for exactly as long as the caller holds it. Nothing
here writes one to the table, to a log, or to an exception message. A
credential store that can show you an old key is a credential store whose
one read is worth all five tenants.
"""

import re
import secrets
from datetime import datetime, timezone

import psycopg

from nexus.ingress.auth import key_digest

#: Table names are spliced into SQL, so they are checked rather than trusted
#: -- the same rule `ledger/pg.py` applies, and for the same reason: once an
#: identifier reaches an f-string, "nobody would pass that" is all that
#: stands between this module and an injection, and that is not a control.
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: Long enough that guessing is not a strategy, and prefixed so a leaked
#: string is recognisable as a nexus credential in a log someone is scanning.
_KEY_BYTES = 32


class TenantKeyStore:
    """Issue, revoke and list tenant credentials.

    `table` exists so tests have somewhere to be destructive without touching
    the real credential table -- the same reason `PgLedger` takes one.
    """

    def __init__(self, dsn: str, table: str = "tenant_key") -> None:
        if not _IDENT.match(table):
            raise ValueError(f"unsafe table identifier: {table!r}")
        self.dsn = dsn
        self.table = table

    def issue(self, tenant: str, label: str, issued_by: str) -> tuple[str, str]:
        """Mint a credential. Returns `(key_id, plaintext)` -- plaintext once.

        The caller is the only party that will ever see the plaintext again,
        which is why the return type carries it out rather than a later read
        being able to fetch it.
        """
        plaintext = f"nx-{secrets.token_urlsafe(_KEY_BYTES)}"
        key_id = f"k-{secrets.token_hex(8)}"
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                f"INSERT INTO {self.table} (key_id, tenant, key_sha256, "
                "key_prefix, label, issued_by, issued_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    key_id,
                    tenant,
                    key_digest(plaintext),
                    plaintext[:8],
                    label,
                    issued_by,
                    datetime.now(timezone.utc),
                ),
            )
        return key_id, plaintext

    def revoke(self, key_id: str, revoked_by: str) -> None:
        """Mark a credential dead. The row stays; only `revoked_at` moves.

        There is deliberately no un-revoke. Reviving a revoked key would put
        a credential that may already have leaked back into service, and the
        cheap alternative -- issue a new one -- has none of that doubt.
        """
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                f"UPDATE {self.table} SET revoked_at = %s, revoked_by = %s "
                "WHERE key_id = %s AND revoked_at IS NULL",
                (datetime.now(timezone.utc), revoked_by, key_id),
            )

    def active_digests(self) -> dict[str, str]:
        """Live credentials as `sha256 -> tenant`, shaped for the key index."""
        with psycopg.connect(self.dsn) as conn:
            rows = conn.execute(
                f"SELECT key_sha256, tenant FROM {self.table} "
                "WHERE revoked_at IS NULL"
            ).fetchall()
        return {digest: tenant for digest, tenant in rows}

    def list_for_console(self, tenant: str | None = None) -> list[dict]:
        """What the console may display: prefix and status, never the secret.

        `key_sha256` is not returned either. It is not the key, but it is a
        verifier for one, and an offline guess against a leaked digest is
        cheaper than against a live endpoint that logs failures.
        """
        sql = (
            f"SELECT key_id, tenant, key_prefix, label, issued_by, issued_at, "
            f"revoked_by, revoked_at FROM {self.table}"
        )
        params: tuple = ()
        if tenant is not None:
            sql += " WHERE tenant = %s"
            params = (tenant,)
        sql += " ORDER BY issued_at DESC"
        with psycopg.connect(self.dsn) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            {
                "key_id": r[0],
                "tenant": r[1],
                "key_prefix": r[2],
                "label": r[3],
                "issued_by": r[4],
                "issued_at": r[5],
                "revoked_by": r[6],
                "revoked_at": r[7],
                "state": "revoked" if r[7] is not None else "active",
            }
            for r in rows
        ]

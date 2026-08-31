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
from psycopg.types.json import Jsonb

from nexus.ingress.auth import key_digest
from nexus.registry.effective import Override

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


class ControlPlaneStore:
    """Overrides, budgets and the action log.

    One class rather than three because they are read together on every
    assembly and written together under one version check -- splitting them
    would put the concurrency rule in a place that can only see half of what
    it guards.

    Table names are parameterised for the same reason `TenantKeyStore` and
    `PgLedger` parameterise theirs: the destructive tests need somewhere to
    be destructive that is not the table the running gateway reads.
    """

    def __init__(
        self,
        dsn: str,
        overrides_table: str = "policy_override",
        budgets_table: str = "tenant_budget",
        audit_table: str = "admin_action",
    ) -> None:
        for name in (overrides_table, budgets_table, audit_table):
            if not _IDENT.match(name):
                raise ValueError(f"unsafe table identifier: {name!r}")
        self.dsn = dsn
        self.overrides_table = overrides_table
        self.budgets_table = budgets_table
        self.audit_table = audit_table

    # --- overrides -------------------------------------------------------

    def active_overrides(self) -> list[Override]:
        """Every override still in force, shaped for `compose()`."""
        with psycopg.connect(self.dsn) as conn:
            rows = conn.execute(
                f"SELECT tenant, field, model, removed_value FROM "
                f"{self.overrides_table} WHERE lifted_at IS NULL"
            ).fetchall()
        return [
            Override(tenant=t, field=f, removed_value=v, model=m)
            for t, f, m, v in rows
        ]

    def apply_override(
        self, ov: Override, reason: str, applied_by: str
    ) -> int:
        with psycopg.connect(self.dsn) as conn:
            row = conn.execute(
                f"INSERT INTO {self.overrides_table} (tenant, field, model, "
                "removed_value, reason, applied_by, applied_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    ov.tenant,
                    ov.field,
                    ov.model,
                    ov.removed_value,
                    reason,
                    applied_by,
                    datetime.now(timezone.utc),
                ),
            ).fetchone()
        return row[0]

    def lift_override(self, override_id: int, lifted_by: str) -> None:
        """The inverse action. Every hot change is required to have one."""
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                f"UPDATE {self.overrides_table} SET lifted_at = %s, "
                "lifted_by = %s WHERE id = %s AND lifted_at IS NULL",
                (datetime.now(timezone.utc), lifted_by, override_id),
            )

    def list_overrides(self, tenant: str | None = None) -> list[dict]:
        sql = (
            f"SELECT id, tenant, field, model, removed_value, reason, "
            f"applied_by, applied_at, lifted_by, lifted_at "
            f"FROM {self.overrides_table}"
        )
        params: tuple = ()
        if tenant is not None:
            sql += " WHERE tenant = %s"
            params = (tenant,)
        sql += " ORDER BY applied_at DESC"
        with psycopg.connect(self.dsn) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            {
                "id": r[0], "tenant": r[1], "field": r[2], "model": r[3],
                "removed_value": r[4], "reason": r[5], "applied_by": r[6],
                "applied_at": r[7], "lifted_by": r[8], "lifted_at": r[9],
                "state": "lifted" if r[9] is not None else "in_force",
            }
            for r in rows
        ]

    # --- budgets ---------------------------------------------------------

    def current_budgets(self) -> dict[str, int]:
        """Each tenant's newest budget row. Append-only, so newest wins."""
        with psycopg.connect(self.dsn) as conn:
            rows = conn.execute(
                f"SELECT DISTINCT ON (tenant) tenant, budget_nanousd_per_day "
                f"FROM {self.budgets_table} ORDER BY tenant, ts DESC, id DESC"
            ).fetchall()
        return {tenant: budget for tenant, budget in rows}

    def set_budget(
        self,
        tenant: str,
        budget: int,
        reason: str,
        changed_by: str,
        approved_by: str | None = None,
    ) -> None:
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                f"INSERT INTO {self.budgets_table} (tenant, "
                "budget_nanousd_per_day, reason, changed_by, approved_by, ts) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    tenant,
                    budget,
                    reason,
                    changed_by,
                    approved_by,
                    datetime.now(timezone.utc),
                ),
            )

    def budget_history(self, tenant: str) -> list[dict]:
        with psycopg.connect(self.dsn) as conn:
            rows = conn.execute(
                f"SELECT budget_nanousd_per_day, reason, changed_by, "
                f"approved_by, ts FROM {self.budgets_table} WHERE tenant = %s "
                "ORDER BY ts DESC",
                (tenant,),
            ).fetchall()
        return [
            {
                "budget_nanousd_per_day": r[0], "reason": r[1],
                "changed_by": r[2], "approved_by": r[3], "ts": r[4],
            }
            for r in rows
        ]

    # --- concurrency -----------------------------------------------------

    def policy_version(self, tenant: str) -> str:
        """A tenant's control-plane version, spanning both mutable tables.

        Both, because looking at only one lets two administrators -- one
        editing capabilities, one editing the budget -- each believe they
        hold the current state. Two people editing the same tenant at once is
        not an edge case in a platform this size; it is Tuesday afternoon.
        """
        with psycopg.connect(self.dsn) as conn:
            (o,) = conn.execute(
                f"SELECT coalesce(max(id), 0) FROM {self.overrides_table} "
                "WHERE tenant = %s",
                (tenant,),
            ).fetchone()
            (b,) = conn.execute(
                f"SELECT coalesce(max(id), 0) FROM {self.budgets_table} "
                "WHERE tenant = %s",
                (tenant,),
            ).fetchone()
        return f"{o}.{b}"

    # --- audit -----------------------------------------------------------

    def record(
        self,
        actor: str,
        action: str,
        target: str | None = None,
        before: dict | None = None,
        after: dict | None = None,
    ) -> None:
        """Append to the action log. Never called with a credential in it."""
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                f"INSERT INTO {self.audit_table} (actor, action, target, "
                "before, after, ts) VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    actor,
                    action,
                    target,
                    Jsonb(before) if before is not None else None,
                    Jsonb(after) if after is not None else None,
                    datetime.now(timezone.utc),
                ),
            )

    def recent_actions(self, limit: int = 100) -> list[dict]:
        with psycopg.connect(self.dsn) as conn:
            rows = conn.execute(
                f"SELECT actor, action, target, before, after, ts FROM "
                f"{self.audit_table} ORDER BY ts DESC, id DESC LIMIT %s",
                (limit,),
            ).fetchall()
        return [
            {
                "actor": r[0], "action": r[1], "target": r[2],
                "before": r[3], "after": r[4], "ts": r[5],
            }
            for r in rows
        ]


class ChangeRequestStore:
    """Requests to loosen something. Records asking, never granting.

    There is no `status` column and that is the design. Status is derived by
    looking at what `policies/<tenant>.yaml` says *now*: if the requested
    value is in the declared policy, the change shipped; if it is not, it is
    still waiting. A status field somebody can click to "done" eventually
    has a change marked complete that never happened, and this console
    already refuses that shape of lie elsewhere -- an empty ledger is not a
    pass, and an unchecked gate has not started passing.
    """

    def __init__(self, dsn: str, table: str = "change_request") -> None:
        if not _IDENT.match(table):
            raise ValueError(f"unsafe table identifier: {table!r}")
        self.dsn = dsn
        self.table = table

    def record(
        self,
        tenant: str,
        kind: str,
        payload: str,
        reason: str,
        requested_by: str,
        field: str | None = None,
        model: str | None = None,
        value: str | None = None,
    ) -> int:
        with psycopg.connect(self.dsn) as conn:
            row = conn.execute(
                f"INSERT INTO {self.table} (tenant, kind, field, model, value, "
                "reason, payload, requested_by, requested_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    tenant, kind, field, model, value, reason, payload,
                    requested_by, datetime.now(timezone.utc),
                ),
            ).fetchone()
        return row[0]

    def list_requests(self, declared: dict) -> list[dict]:
        """Every request, with its status derived from the policy files."""
        with psycopg.connect(self.dsn) as conn:
            rows = conn.execute(
                f"SELECT id, tenant, kind, field, model, value, reason, payload, "
                f"requested_by, requested_at FROM {self.table} "
                "ORDER BY requested_at DESC"
            ).fetchall()
        out = []
        for r in rows:
            item = {
                "id": r[0], "tenant": r[1], "kind": r[2], "field": r[3],
                "model": r[4], "value": r[5], "reason": r[6], "payload": r[7],
                "requested_by": r[8], "requested_at": r[9],
            }
            item["state"] = _shipped(item, declared) and "shipped" or "pending"
            out.append(item)
        return out


def _shipped(req: dict, declared: dict) -> bool:
    """Has this request landed in the policy files yet?

    Read-only and derived on every listing, so the answer cannot drift from
    the thing it describes.
    """
    policy = declared.get(req["tenant"])
    if req["kind"] == "new_tenant":
        return policy is not None
    if policy is None:
        return False
    if req["field"] == "cross_tenant_read":
        return req["value"] in policy.cross_tenant_read
    if req["field"] == "substitutable_to":
        model = policy.models.get(req["model"])
        return model is not None and req["value"] in model.substitutable_to
    if req["field"] == "allow_fallback":
        return bool(policy.allow_fallback)
    return False

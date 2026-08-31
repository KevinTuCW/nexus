-- nexus cost ledger. Postgres wiring lands in P2; the table is authored
-- here in P1 so the schema and the in-memory Entry cannot drift apart
-- unnoticed.
--
-- cost_nanousd is BIGINT, never NUMERIC or DOUBLE PRECISION. Money in this
-- system is integer nano-USD end to end (see src/nexus/money.py): cents
-- round every per-token cost to zero, and floats drift over the hundreds of
-- thousands of rows that gate G2 sums.
CREATE TABLE IF NOT EXISTS ledger_entry (
    entry_id        TEXT PRIMARY KEY,
    call_id         TEXT NOT NULL,
    tenant          TEXT NOT NULL,
    workload        TEXT NOT NULL,
    -- NULL for zero-touch tenants, which cannot propagate a trace root
    -- because their repos are unmodified.
    trace_root      TEXT,
    span_id         TEXT NOT NULL,
    parent_span_id  TEXT,
    model           TEXT NOT NULL,
    family          TEXT NOT NULL,
    prompt_tokens       INTEGER NOT NULL,
    completion_tokens   INTEGER NOT NULL,
    cache_write_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens   INTEGER NOT NULL DEFAULT 0,
    cost_nanousd    BIGINT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('ok', 'aborted', 'failed')),
    ts              TIMESTAMPTZ NOT NULL,
    -- Set when a fallback displaced the originally routed model. NULL is
    -- the common case and means "served as routed", not "unknown".
    fallback_from   TEXT,
    -- The model chain: requested -> routed -> served (the `model` column).
    -- Gates G1 and G4 each judge one hop. Nullable because rows written
    -- before Phase 3b have neither, and history must stay readable.
    requested_model TEXT,
    routed_model    TEXT
);

CREATE INDEX IF NOT EXISTS ledger_entry_tenant_ts ON ledger_entry (tenant, ts);
CREATE INDEX IF NOT EXISTS ledger_entry_trace ON ledger_entry (trace_root);
CREATE UNIQUE INDEX IF NOT EXISTS ledger_entry_call ON ledger_entry (call_id);

-- Who read whose usage. No amounts: see src/nexus/audit.py for why.
CREATE TABLE IF NOT EXISTS cross_tenant_read_audit (
    id       BIGSERIAL PRIMARY KEY,
    caller   TEXT NOT NULL,
    targets  TEXT[] NOT NULL,
    denied   BOOLEAN NOT NULL DEFAULT FALSE,
    ts       TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS cross_tenant_read_audit_caller_ts
    ON cross_tenant_read_audit (caller, ts);

-- Issued tenant credentials. Never stores plaintext: the plaintext is
-- returned once, in the response to the call that created it, and exists
-- nowhere afterwards. Rows are never deleted -- dropping a revoked key
-- deletes the answer to "who made that call in March" while the ledger still
-- holds the money it cost.
CREATE TABLE IF NOT EXISTS tenant_key (
    key_id      TEXT PRIMARY KEY,
    tenant      TEXT NOT NULL,
    -- UNIQUE is the invariant, not a hint: two tenants sharing a key would
    -- make attribution ambiguous and the ledger would confidently bill one
    -- for the other's traffic.
    key_sha256  TEXT NOT NULL UNIQUE,
    -- First 8 characters, so a human can tell two live keys apart in the
    -- console without either being recoverable from what is displayed.
    key_prefix  TEXT NOT NULL,
    label       TEXT NOT NULL,
    issued_by   TEXT NOT NULL,
    issued_at   TIMESTAMPTZ NOT NULL,
    revoked_by  TEXT,
    -- NULL means live. Revocation is a timestamp rather than a delete so the
    -- window a key was valid in stays answerable.
    revoked_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS tenant_key_tenant ON tenant_key (tenant);

-- Capability overrides. Records what was *removed*; there is deliberately no
-- `new_value` column. Loosening therefore has no syntax here and can only be
-- done by editing policies/<tenant>.yaml and going through review. A comment
-- asking people not to widen would not survive the next person who needs to.
CREATE TABLE IF NOT EXISTS policy_override (
    id            BIGSERIAL PRIMARY KEY,
    tenant        TEXT NOT NULL,
    field         TEXT NOT NULL CHECK (field IN
                    ('substitutable_to', 'cross_tenant_read',
                     'allow_fallback', 'enabled')),
    -- Only for substitutable_to: which model's table this hangs under.
    model         TEXT,
    removed_value TEXT NOT NULL,
    -- NOT NULL on purpose. A tightening with no stated reason is one nobody
    -- dares lift six months later, so it stops being reversible in practice.
    reason        TEXT NOT NULL,
    applied_by    TEXT NOT NULL,
    applied_at    TIMESTAMPTZ NOT NULL,
    lifted_by     TEXT,
    -- NULL means in force. Lifting is the inverse action every hot change is
    -- required to have.
    lifted_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS policy_override_tenant ON policy_override (tenant);

-- Budgets. A separate table from policy_override because budget must be able
-- to go *up*, and policy_override is built so that nothing can. Append-only:
-- the current budget is the tenant's newest row.
CREATE TABLE IF NOT EXISTS tenant_budget (
    id          BIGSERIAL PRIMARY KEY,
    tenant      TEXT NOT NULL,
    budget_nanousd_per_day BIGINT NOT NULL CHECK (budget_nanousd_per_day >= 0),
    reason      TEXT NOT NULL,
    changed_by  TEXT NOT NULL,
    -- Set when a raise crossed the threshold that needs a second pair of
    -- eyes. NULL on any change that did not.
    approved_by TEXT,
    ts          TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS tenant_budget_tenant_ts ON tenant_budget (tenant, ts);

-- Who changed what. Separate from cross_tenant_read_audit: that one records
-- who *looked*, this one records who *acted*. Neither holds a credential --
-- see src/nexus/admin/store.py.
CREATE TABLE IF NOT EXISTS admin_action (
    id      BIGSERIAL PRIMARY KEY,
    actor   TEXT NOT NULL,
    action  TEXT NOT NULL,
    target  TEXT,
    before  JSONB,
    after   JSONB,
    ts      TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS admin_action_ts ON admin_action (ts DESC);

-- Control-plane accounts. Replaces the shared-key mechanism entirely: a key
-- in a query string reaches server logs and `Referer` headers, and a browser
-- address bar cannot set a header, so key-in-URL was the only way that
-- scheme could work in a browser at all.
--
-- Passwords are scrypt-hashed with a per-account salt. Never reversible,
-- never logged, never returned by any endpoint.
CREATE TABLE IF NOT EXISTS admin_account (
    username      TEXT PRIMARY KEY,
    -- scrypt(password, salt, n=2^14, r=8, p=1, dklen=32), hex encoded.
    password_hash TEXT NOT NULL,
    salt          TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('rw', 'ro')),
    created_at    TIMESTAMPTZ NOT NULL,
    last_login_at TIMESTAMPTZ,
    -- Throttling is per account, not global: a brute-force run against one
    -- name must not lock every other administrator out of the console at
    -- exactly the moment somebody is attacking it.
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until  TIMESTAMPTZ,
    disabled_at   TIMESTAMPTZ
);

-- Sessions, server-side. The cookie carries an opaque token and nothing
-- else; the table decides what it means. That makes a session revocable --
-- a signed cookie holding its own claims is valid until it expires no
-- matter what the server learns in the meantime.
CREATE TABLE IF NOT EXISTS admin_session (
    token_sha256 TEXT PRIMARY KEY,
    username     TEXT NOT NULL REFERENCES admin_account(username),
    created_at   TIMESTAMPTZ NOT NULL,
    expires_at   TIMESTAMPTZ NOT NULL,
    revoked_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS admin_session_username ON admin_session (username);

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

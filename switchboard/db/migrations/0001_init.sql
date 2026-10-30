-- Tenants, API keys and the usage ledger.

CREATE TABLE IF NOT EXISTS tenants (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    tier        TEXT NOT NULL DEFAULT 'standard',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS api_keys (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    key_prefix      TEXT NOT NULL,
    key_hash        TEXT NOT NULL UNIQUE,
    role            TEXT NOT NULL CHECK (role IN ('admin', 'developer', 'viewer')),
    rate_limit_rpm  INTEGER,
    rate_limit_tpm  INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS api_keys_prefix_idx ON api_keys (key_prefix);
CREATE INDEX IF NOT EXISTS api_keys_tenant_idx ON api_keys (tenant_id);

-- One row per request. Partitioned by month; partitions are created by the application
-- (see switchboard.db.migrate.ensure_usage_partitions). The DEFAULT partition catches
-- anything written before a month's partition exists so no event is ever lost.
CREATE TABLE IF NOT EXISTS usage_events (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY,
    ts                 TIMESTAMPTZ NOT NULL,
    request_id         TEXT NOT NULL,
    tenant_id          TEXT NOT NULL,
    api_key_id         TEXT,
    requested_model    TEXT NOT NULL,
    deployment_id      TEXT,
    provider           TEXT,
    task_class         TEXT,
    route_mode         TEXT NOT NULL DEFAULT 'explicit',
    route_reason       TEXT,
    experiment_id      TEXT,
    cache_status       TEXT NOT NULL DEFAULT 'miss',
    stream             BOOLEAN NOT NULL DEFAULT false,
    prompt_tokens      INTEGER NOT NULL DEFAULT 0,
    completion_tokens  INTEGER NOT NULL DEFAULT 0,
    cost_usd           NUMERIC(14, 8) NOT NULL DEFAULT 0,
    ttft_ms            INTEGER,
    total_ms           INTEGER NOT NULL,
    status             TEXT NOT NULL,
    error_code         TEXT,
    PRIMARY KEY (id, ts)
) PARTITION BY RANGE (ts);

CREATE TABLE IF NOT EXISTS usage_events_default PARTITION OF usage_events DEFAULT;

CREATE INDEX IF NOT EXISTS usage_events_tenant_ts_idx ON usage_events (tenant_id, ts);
CREATE INDEX IF NOT EXISTS usage_events_deployment_ts_idx ON usage_events (deployment_id, ts);

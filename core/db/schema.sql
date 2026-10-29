-- ZizkaDB Schema
-- Enable extensions
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ─────────────────────────────────────────
-- TENANTS (one per project/API key)
-- ─────────────────────────────────────────
CREATE TABLE tenants (
    tenant_id   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        VARCHAR(255) NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────
-- AGENTS
-- ─────────────────────────────────────────
CREATE TABLE agents (
    agent_id    VARCHAR(255) NOT NULL,
    tenant_id   UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    first_seen  TIMESTAMPTZ DEFAULT NOW(),
    last_seen   TIMESTAMPTZ DEFAULT NOW(),
    event_count BIGINT DEFAULT 0,
    metadata    JSONB,
    retention_days INTEGER,  -- NULL = EVENT_RETENTION_DAYS, else kept forever (services/retention.py)
    -- Newest chain link, for the chain verifier only (services/audit_chain.py).
    -- NULL until the agent's first write after the upgrade that added it;
    -- never used to compute a link.
    chain_head_seq  BIGINT,
    chain_head_hash VARCHAR(64),
    PRIMARY KEY (agent_id, tenant_id)
);

CREATE INDEX idx_agents_tenant ON agents (tenant_id, last_seen DESC);

-- ─────────────────────────────────────────
-- API KEYS
-- ─────────────────────────────────────────
CREATE TABLE api_keys (
    key_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id   UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    agent_id    VARCHAR(255),  -- NULL = legacy tenant-wide key; set = scoped to one agent
    key_hash    VARCHAR(255) NOT NULL UNIQUE,  -- SHA-256 hashed
    key_prefix  VARCHAR(24) NOT NULL,           -- e.g. "zizkadb_live_xxxx" for display
    name        VARCHAR(255),
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    last_used   TIMESTAMPTZ,
    revoked     BOOLEAN DEFAULT FALSE,
    scope       VARCHAR(16) NOT NULL DEFAULT 'read_write',  -- read_only keys never write
    expires_at  TIMESTAMPTZ,                                 -- NULL = never expires
    CONSTRAINT fk_api_keys_agent FOREIGN KEY (agent_id, tenant_id)
        REFERENCES agents (agent_id, tenant_id) ON DELETE CASCADE
);

CREATE INDEX idx_api_keys_hash ON api_keys (key_hash);

-- ─────────────────────────────────────────
-- USERS
-- ─────────────────────────────────────────
CREATE TABLE users (
    user_id                 UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email                   VARCHAR(255) UNIQUE NOT NULL,
    tenant_id               UUID REFERENCES tenants(tenant_id),
    plan                    VARCHAR(50),   -- pro | team | starter
    subscription_status     VARCHAR(50),   -- trialing | active | past_due | canceled
    trial_ends_at           TIMESTAMPTZ,
    -- DEPRECATED: legacy columns from a removed Stripe integration.
    -- No Python code reads or writes these. Retained to avoid DDL on a live table.
    -- Candidate for removal in a future schema cleanup migration.
    stripe_customer_id      VARCHAR(255) UNIQUE,
    stripe_subscription_id  VARCHAR(255) UNIQUE,
    retention_trial_used    BOOLEAN NOT NULL DEFAULT FALSE,
    gdpr_consent_at         TIMESTAMPTZ,
    marketing_consent       BOOLEAN NOT NULL DEFAULT FALSE,
    marketing_consent_at    TIMESTAMPTZ,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    last_login              TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_users_subscription_status ON users (subscription_status);

-- ─────────────────────────────────────────
-- AUTH OTPs (passwordless login)
-- ─────────────────────────────────────────
CREATE TABLE auth_otps (
    otp_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email       VARCHAR(255) NOT NULL,
    otp_hash    VARCHAR(255) NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    used        BOOLEAN DEFAULT FALSE,
    attempts    INT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_otps_email ON auth_otps (email, expires_at);

-- ─────────────────────────────────────────
-- EVENTS (core append-only table)
-- ─────────────────────────────────────────
CREATE TABLE events (
    event_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    agent_id        VARCHAR(255) NOT NULL,
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event_type      VARCHAR(100) NOT NULL,
    data            JSONB NOT NULL,
    embedding       vector(1536),
    parent_event_id UUID,                               -- causal link (soft: the parent may be deleted by retention)
    session_id      VARCHAR(255),                       -- group related events
    sequence_no     BIGSERIAL,                          -- monotonic, never gaps
    checksum        VARCHAR(64),                        -- SHA-256; v2 covers the audit fields (services/checksum.py)
    metadata        JSONB,
    -- Embedding pipeline state (also added by init_db() on existing installs).
    index_status    VARCHAR(16) NOT NULL DEFAULT 'skipped',
    index_attempts  INTEGER NOT NULL DEFAULT 0,
    index_retry_at  TIMESTAMPTZ,
    idempotency_key VARCHAR(255),                       -- retried writes return the first event
    chain_hash      VARCHAR(64),                        -- per-agent hash chain (services/audit_chain.py)

    CONSTRAINT fk_agent FOREIGN KEY (agent_id, tenant_id)
        REFERENCES agents (agent_id, tenant_id)
);

-- Indexes
CREATE INDEX idx_events_agent_time
    ON events (tenant_id, agent_id, timestamp DESC);

CREATE INDEX idx_events_parent
    ON events (parent_event_id)
    WHERE parent_event_id IS NOT NULL;

CREATE INDEX idx_events_session
    ON events (tenant_id, session_id, timestamp ASC)
    WHERE session_id IS NOT NULL;

CREATE INDEX idx_events_type
    ON events (tenant_id, event_type, timestamp DESC);

-- One agent's STATE_SET / STATE_DELETE events, for time travel (GET /v1/events/at)
CREATE INDEX IF NOT EXISTS idx_events_state_writes
    ON events (tenant_id, agent_id, timestamp)
    WHERE event_type IN ('STATE_SET', 'STATE_DELETE');

CREATE INDEX idx_events_data
    ON events USING gin (data);

CREATE UNIQUE INDEX uq_events_idempotency
    ON events (tenant_id, agent_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- Keyword search (services/keyword_search.py FTS_DOCUMENT; keep identical).
CREATE INDEX idx_events_fts
    ON events USING gin ((to_tsvector('simple', event_type) || jsonb_to_tsvector('simple', data, '["string", "numeric"]')));

-- Explicit supersedes links (services/memory_rank.py).
CREATE INDEX idx_events_supersedes
    ON events (tenant_id, (metadata->>'supersedes'))
    WHERE metadata ? 'supersedes';

-- Chain links of deleted events (GDPR forget, retention): the hash, never the content.
CREATE TABLE event_tombstones (
    tenant_id   UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    agent_id    VARCHAR(255) NOT NULL,
    event_id    UUID NOT NULL,
    sequence_no BIGINT NOT NULL,
    chain_hash  VARCHAR(64) NOT NULL,
    reason      VARCHAR(50) NOT NULL,
    deleted_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- The deleted row's checksum, so the verifier can recompute this link.
    -- NULL only on tombstones written before the upgrade that added it
    -- (see tombstone_checksum_cutoff).
    checksum    VARCHAR(64),
    -- Erasure receipts (reason 'erasure_receipt', ADR-009 H3) are rows here too,
    -- so older images link past them: checksum holds the digest of the
    -- tombstones they cover, erased_count how many; each covered tombstone
    -- names its receipt in receipt_id.
    erased_count INTEGER,
    receipt_id   UUID,
    PRIMARY KEY (tenant_id, event_id)
);

CREATE INDEX idx_event_tombstones_agent_seq
    ON event_tombstones (tenant_id, agent_id, sequence_no DESC);

-- Kept when a write finds the agent's recorded newest link missing or changed:
-- the newest events were removed or edited outside an erasure, and the new
-- event would otherwise link past the gap (services/audit_chain.py).
CREATE TABLE IF NOT EXISTS chain_head_breaks (
    id            BIGSERIAL PRIMARY KEY,
    tenant_id     UUID NOT NULL,
    agent_id      VARCHAR(255) NOT NULL,
    expected_seq  BIGINT NOT NULL,
    expected_hash VARCHAR(64),
    found_seq     BIGINT,
    found_hash    VARCHAR(64),
    detected_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (agent_id, tenant_id) REFERENCES agents (agent_id, tenant_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chain_head_breaks_agent ON chain_head_breaks (tenant_id, agent_id);

-- When tombstones started carrying a checksum; init_db() records it once,
-- after the trigger below exists. A tombstone without a checksum deleted after
-- it is reported by the chain verifier (services/audit_chain.py).
CREATE TABLE IF NOT EXISTS tombstone_checksum_cutoff (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    since     TIMESTAMPTZ NOT NULL
);

-- Fills a tombstone's checksum from the row being deleted when the writer did
-- not: an older image (rolling deploy, downgrade) inserts the tombstone before
-- deleting the row, so its tombstones stay checkable. Created only if missing
-- by init_db(); a different body needs a new function name.
CREATE OR REPLACE FUNCTION zizkadb_tombstone_checksum() RETURNS trigger AS $fn$
BEGIN
    IF NEW.checksum IS NULL THEN
        SELECT checksum INTO NEW.checksum FROM events
        WHERE tenant_id = NEW.tenant_id AND event_id = NEW.event_id;
    END IF;
    RETURN NEW;
END
$fn$ LANGUAGE plpgsql;

CREATE TRIGGER trg_tombstone_checksum BEFORE INSERT ON event_tombstones
    FOR EACH ROW EXECUTE FUNCTION zizkadb_tombstone_checksum();

-- Checksum verification pages an agent's events by sequence_no.
CREATE INDEX idx_events_agent_seq
    ON events (tenant_id, agent_id, sequence_no DESC);

-- Newest event that may carry a v1 (payload-only) checksum; init_db() records
-- it once. A fresh install has no legacy events.
CREATE TABLE IF NOT EXISTS checksum_v2_cutoff (
    singleton        BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    legacy_until_seq BIGINT NOT NULL,
    recorded_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
INSERT INTO checksum_v2_cutoff (legacy_until_seq) VALUES (0)
ON CONFLICT (singleton) DO NOTHING;

-- Vector index (HNSW — better recall than IVFFlat)
CREATE INDEX idx_events_embedding
    ON events USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ─────────────────────────────────────────
-- USAGE METERING
-- ─────────────────────────────────────────
CREATE TABLE usage_daily (
    tenant_id       UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    date            DATE NOT NULL,
    events_written  BIGINT DEFAULT 0,
    -- NOTE: queries_run and searches_run are reserved for future metering.
    -- Only events_written is currently incremented (core/services/event_write.py).
    queries_run     BIGINT DEFAULT 0,
    searches_run    BIGINT DEFAULT 0,
    PRIMARY KEY (tenant_id, date)
);

-- ─────────────────────────────────────────
-- SDK TELEMETRY (anonymous install pings — no PII)
-- (also created at runtime via init_db() in core/db/connection.py)
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sdk_telemetry (
    install_id   VARCHAR(64) NOT NULL,
    sdk          VARCHAR(50) NOT NULL DEFAULT 'unknown',
    sdk_version  VARCHAR(30),
    runtime      VARCHAR(50),
    os           VARCHAR(50),
    mode         VARCHAR(30),
    country_code CHAR(2),
    first_seen   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ping_count   INT NOT NULL DEFAULT 1,
    PRIMARY KEY (install_id, sdk)
);

CREATE TABLE IF NOT EXISTS sdk_update_subscriptions (
    subscription_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email           VARCHAR(255) NOT NULL,
    install_id      TEXT,
    sdk             VARCHAR(32) NOT NULL DEFAULT 'unknown',
    country_code    CHAR(2),
    source          VARCHAR(64) NOT NULL DEFAULT 'dashboard',
    user_agent      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_sdk_update_subscriptions_email
    ON sdk_update_subscriptions (LOWER(email));

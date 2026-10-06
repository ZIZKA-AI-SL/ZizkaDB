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
    parent_event_id UUID REFERENCES events(event_id),  -- causal link
    session_id      VARCHAR(255),                       -- group related events
    sequence_no     BIGSERIAL,                          -- monotonic, never gaps
    checksum        VARCHAR(64),                        -- SHA-256 of event content
    metadata        JSONB,
    chain_hash      VARCHAR(64),                        -- per-tenant hash chain (services/audit_chain.py)
    prev_chain_hash VARCHAR(64),

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

CREATE INDEX idx_events_data
    ON events USING gin (data);

CREATE INDEX idx_events_tenant_chain
    ON events (tenant_id, sequence_no)
    WHERE chain_hash IS NOT NULL;

-- Vector index (HNSW — better recall than IVFFlat)
CREATE INDEX idx_events_embedding
    ON events USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Chain links of lawfully erased events (GDPR forget, agent delete): hashes
-- only, no content, so the tenant's chain stays verifiable after erasure.
CREATE TABLE IF NOT EXISTS event_erasures (
    event_id        UUID PRIMARY KEY,
    tenant_id       UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    sequence_no     BIGINT NOT NULL,
    chain_hash      VARCHAR(64) NOT NULL,
    prev_chain_hash VARCHAR(64),
    reason          VARCHAR(32) NOT NULL,
    erased_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_event_erasures_tenant_seq
    ON event_erasures (tenant_id, sequence_no);

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

import asyncio
import logging
import os
from urllib.parse import quote, urlsplit, urlunsplit

import asyncpg
import redis.asyncio as redis
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, VectorParams

from services.runtime_env import is_dev_mode

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------
# Global connection holders
# --------------------------------------------------------------------

_pg_pool: asyncpg.Pool | None = None
_redis: redis.Redis | None = None
_qdrant: AsyncQdrantClient | None = None

QDRANT_COLLECTION = "agent_events"
VECTOR_SIZE = 1536  # OpenAI text-embedding-3-small

# Matches infra/docker-compose.yml (`uvicorn --workers 4`). Override with
# WEB_CONCURRENCY or UVICORN_WORKERS if you change the process count.
PG_POOL_MAX_SIZE = 20
PG_DEFAULT_MAX_CONNECTIONS = 100


def uvicorn_worker_count() -> tuple[int, bool]:
    """Return (worker_count, assumed).

    Compose production uses ``uvicorn --workers 4``. Local ``uvicorn --reload``
    is one process. Only assume 4 when ``ENV=production`` and neither
    ``WEB_CONCURRENCY`` nor ``UVICORN_WORKERS`` is set.
    """
    raw = os.getenv("WEB_CONCURRENCY") or os.getenv("UVICORN_WORKERS")
    if raw:
        try:
            return max(int(raw), 1), False
        except ValueError:
            pass
    if os.getenv("ENV", "").strip().lower() == "production":
        return 4, True
    return 1, True


def warn_if_pool_near_pg_limit(
    max_size: int,
    workers: int,
    pg_max_connections: int = PG_DEFAULT_MAX_CONNECTIONS,
    *,
    assumed: bool = False,
) -> str | None:
    """Return a warning if pool × workers is ≥ 80% of typical max_connections."""
    potential = max_size * max(workers, 1)
    if potential < int(pg_max_connections * 0.8):
        return None
    worker_bit = f"workers={workers}"
    if assumed:
        worker_bit += " (assumed; set WEB_CONCURRENCY)"
    return (
        f"Postgres pool max_size={max_size} × {worker_bit} = {potential} "
        f"connections; default max_connections={pg_max_connections}. "
        "Add PgBouncer before scaling workers."
    )


# --------------------------------------------------------------------
# Initialize all databases
# --------------------------------------------------------------------

def redis_url() -> str:
    """REDIS_URL, with REDIS_PASSWORD added when set and the URL carries none."""
    url = os.getenv("REDIS_URL", "redis://localhost:6379")
    password = (os.getenv("REDIS_PASSWORD") or "").strip()
    if not password:
        return url
    parts = urlsplit(url)
    if parts.password or "@" in parts.netloc:
        return url
    return urlunsplit(parts._replace(netloc=f":{quote(password, safe='')}@{parts.netloc}"))


def qdrant_api_key() -> str | None:
    return (os.getenv("QDRANT_API_KEY") or "").strip() or None


# Passwords published in .env.example and the docs.
_WELL_KNOWN_DB_PASSWORDS = {"zizkadb", "postgres", "password"}


def uses_default_db_password() -> bool:
    """True when production connects to Postgres with a well-known password."""
    if is_dev_mode():
        return False
    try:
        password = urlsplit(os.getenv("DATABASE_URL") or "").password
    except ValueError:
        return False
    return password in _WELL_KNOWN_DB_PASSWORDS


async def _ensure_fts_index() -> None:
    """Full-text index for keyword search (services/keyword_search.py).

    Built CONCURRENTLY so a large events table keeps accepting writes while it
    builds. A build that was interrupted leaves an INVALID index, which
    IF NOT EXISTS would skip forever, so drop and rebuild that one.
    """
    from services.keyword_search import FTS_DOCUMENT

    valid = await _pg_pool.fetchval(
        """
        SELECT i.indisvalid FROM pg_index i
        JOIN pg_class c ON c.oid = i.indexrelid
        WHERE c.relname = 'idx_events_fts'
        """
    )
    if valid:
        return
    if valid is False:
        await _pg_pool.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_events_fts")
    logger.info("Building the keyword search index (idx_events_fts)")
    await _pg_pool.execute(
        f"CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_events_fts ON events USING gin ({FTS_DOCUMENT})"
    )


# Any constant works; it only has to be the same in every worker.
MIGRATION_LOCK_ID = 7_301_884_221


async def _run_migrations():
    await _pg_pool.execute("""
        ALTER TABLE users ADD COLUMN IF NOT EXISTS plan VARCHAR(50);
        ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_status VARCHAR(50);
        ALTER TABLE users ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMPTZ;
        ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_customer_id VARCHAR(255);
        ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_subscription_id VARCHAR(255);
        ALTER TABLE users ADD COLUMN IF NOT EXISTS retention_trial_used BOOLEAN NOT NULL DEFAULT FALSE;
        ALTER TABLE users ADD COLUMN IF NOT EXISTS gdpr_consent_at TIMESTAMPTZ;
        ALTER TABLE users ADD COLUMN IF NOT EXISTS marketing_consent BOOLEAN NOT NULL DEFAULT FALSE;
        ALTER TABLE users ADD COLUMN IF NOT EXISTS marketing_consent_at TIMESTAMPTZ;

        ALTER TABLE auth_otps ADD COLUMN IF NOT EXISTS attempts INT NOT NULL DEFAULT 0;
    """)

    await _pg_pool.execute("""
        UPDATE users
        SET
            plan='pro',
            subscription_status='trialing',
            trial_ends_at=COALESCE(
                trial_ends_at,
                created_at + interval '30 days'
            )
        WHERE
            plan IS NULL
            OR subscription_status IS NULL;
    """)
    await _pg_pool.execute("""

        CREATE TABLE IF NOT EXISTS community_posts (
            post_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            author_name VARCHAR(120) NOT NULL,
            author_email VARCHAR(255),
            category VARCHAR(32) NOT NULL DEFAULT 'question',
            title VARCHAR(300) NOT NULL,
            body TEXT NOT NULL,
            image_urls JSONB NOT NULL DEFAULT '[]'::jsonb,
            reply_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS community_replies (
            reply_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            post_id UUID NOT NULL
                REFERENCES community_posts(post_id)
                ON DELETE CASCADE,
            author_name VARCHAR(120) NOT NULL,
            author_email VARCHAR(255),
            body TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        UPDATE users
        SET subscription_status = 'trialing',
            trial_ends_at = COALESCE(trial_ends_at, NOW() + INTERVAL '30 days')
        WHERE subscription_status = 'pending_checkout'

    """)

    # Upgrade legacy community board schema (pre-marketing-restore installs used
    # image_url/author_ip columns; the restored API expects image_urls JSONB).
    await _pg_pool.execute("""
        ALTER TABLE community_posts ADD COLUMN IF NOT EXISTS author_email VARCHAR(255);
        ALTER TABLE community_posts ADD COLUMN IF NOT EXISTS image_urls JSONB NOT NULL DEFAULT '[]'::jsonb;
        ALTER TABLE community_posts ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
        ALTER TABLE community_replies ADD COLUMN IF NOT EXISTS author_email VARCHAR(255);
    """)
    await _pg_pool.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'community_posts'
                  AND column_name = 'image_url'
            ) THEN
                UPDATE community_posts
                SET image_urls = CASE
                    WHEN image_url IS NOT NULL AND image_url <> ''
                    THEN jsonb_build_array(image_url)
                    ELSE '[]'::jsonb
                END
                WHERE image_urls IS NULL OR image_urls = '[]'::jsonb;
            END IF;
        END $$;
    """)

    await _pg_pool.execute("""
        CREATE INDEX IF NOT EXISTS idx_events_tenant_session_time
        ON events (tenant_id, session_id, timestamp)
        WHERE session_id IS NOT NULL;
    """)

    await _pg_pool.execute("""
        ALTER TABLE events
        ADD COLUMN IF NOT EXISTS index_status VARCHAR(16) NOT NULL DEFAULT 'skipped';
        CREATE INDEX IF NOT EXISTS idx_events_index_status
        ON events (index_status)
        WHERE index_status IN ('pending', 'failed');
        -- Embed worker retry state (workers/embed_worker.py): failed events back
        -- off, and become 'dead' after EMBED_MAX_ATTEMPTS. Constant default, so
        -- no table rewrite.
        ALTER TABLE events
        ADD COLUMN IF NOT EXISTS index_attempts INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE events
        ADD COLUMN IF NOT EXISTS index_retry_at TIMESTAMPTZ;
    """)

    # Event checksum v2 (services/checksum.py). The first start on v2 code
    # records the newest event that may still carry a v1 checksum; v1 is never
    # accepted above it, so a v2 row cannot be downgraded. Later starts keep
    # the original value (ON CONFLICT DO NOTHING).
    await _pg_pool.execute("""
        CREATE TABLE IF NOT EXISTS checksum_v2_cutoff (
            singleton        BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
            legacy_until_seq BIGINT NOT NULL,
            recorded_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        INSERT INTO checksum_v2_cutoff (legacy_until_seq)
        SELECT COALESCE(MAX(sequence_no), 0) FROM events
        ON CONFLICT (singleton) DO NOTHING;
        CREATE INDEX IF NOT EXISTS idx_events_agent_seq
        ON events (tenant_id, agent_id, sequence_no DESC);
    """)

    # Time travel reads only an agent's state writes; without this index, finding them can
    # mean scanning every event in the table.
    await _pg_pool.execute("""
        CREATE INDEX IF NOT EXISTS idx_events_state_writes
        ON events (tenant_id, agent_id, timestamp)
        WHERE event_type IN ('STATE_SET', 'STATE_DELETE');
    """)

    await _pg_pool.execute("""
        ALTER TABLE tenants
        ADD COLUMN IF NOT EXISTS embedding_provider VARCHAR(32)
        NOT NULL DEFAULT 'openai';

        ALTER TABLE tenants
        ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(64)
        NOT NULL DEFAULT 'text-embedding-3-small';

        ALTER TABLE tenants
        ADD COLUMN IF NOT EXISTS embedding_use_platform_key BOOLEAN
        NOT NULL DEFAULT TRUE;

        ALTER TABLE tenants
        ADD COLUMN IF NOT EXISTS embedding_api_key_encrypted TEXT;
    """)

    await _pg_pool.execute("""
        ALTER TABLE api_keys
        ADD COLUMN IF NOT EXISTS agent_id VARCHAR(255);
    """)

    await _pg_pool.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname='fk_api_keys_agent'
            ) THEN
                ALTER TABLE api_keys
                ADD CONSTRAINT fk_api_keys_agent
                FOREIGN KEY (agent_id, tenant_id)
                REFERENCES agents(agent_id, tenant_id)
                ON DELETE CASCADE;
            END IF;
        END $$;
    """)

    await _pg_pool.execute("""
        CREATE INDEX IF NOT EXISTS idx_api_keys_agent
        ON api_keys (tenant_id, agent_id)
        WHERE revoked = FALSE;
    """)

    await _pg_pool.execute("""
        CREATE INDEX IF NOT EXISTS idx_api_keys_tenant_active
        ON api_keys (tenant_id)
        WHERE revoked = FALSE;
    """)

    await _pg_pool.execute("""
        CREATE TABLE IF NOT EXISTS sdk_telemetry (
            install_id TEXT NOT NULL,
            sdk TEXT NOT NULL DEFAULT 'unknown',
            sdk_version TEXT NOT NULL DEFAULT 'unknown',
            runtime TEXT NOT NULL DEFAULT 'unknown',
            os TEXT NOT NULL DEFAULT 'unknown',
            mode TEXT NOT NULL DEFAULT 'cloud',
            country_code CHAR(2),
            first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            ping_count INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (install_id, sdk)
        )
    """)

    await _pg_pool.execute("""
        ALTER TABLE sdk_telemetry ADD COLUMN IF NOT EXISTS country_code CHAR(2);
    """)

    await _pg_pool.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_constraint c
                JOIN pg_class t ON c.conrelid = t.oid
                WHERE t.relname = 'sdk_telemetry'
                  AND c.contype = 'p'
                  AND c.conname = 'sdk_telemetry_pkey'
                  AND array_length(c.conkey, 1) = 1
            ) THEN
                UPDATE sdk_telemetry SET sdk = COALESCE(NULLIF(sdk, ''), 'unknown');
                ALTER TABLE sdk_telemetry DROP CONSTRAINT sdk_telemetry_pkey;
                ALTER TABLE sdk_telemetry ADD PRIMARY KEY (install_id, sdk);
            END IF;
        EXCEPTION
            WHEN duplicate_table THEN NULL;
        END $$;
    """)

    await _pg_pool.execute("""
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
        CREATE INDEX IF NOT EXISTS idx_sdk_update_subscriptions_created
            ON sdk_update_subscriptions (created_at DESC);
    """)




    await _pg_pool.execute("""
        CREATE TABLE IF NOT EXISTS demo_requests (
            request_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            first_name    VARCHAR(80) NOT NULL,
            last_name     VARCHAR(80) NOT NULL,
            company_name  VARCHAR(255) NOT NULL,
            website       VARCHAR(500) NOT NULL,
            ip_address    VARCHAR(64),
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_demo_requests_created
            ON demo_requests (created_at DESC);
    """)

    await _pg_pool.execute("""
        ALTER TABLE demo_requests ADD COLUMN IF NOT EXISTS email VARCHAR(255) NOT NULL DEFAULT '';
    """)

    await _pg_pool.execute("""
        ALTER TABLE demo_requests ADD COLUMN IF NOT EXISTS position VARCHAR(120);
        ALTER TABLE demo_requests ADD COLUMN IF NOT EXISTS source VARCHAR(64);
    """)

    await _pg_pool.execute("""
        CREATE TABLE IF NOT EXISTS marketing_subscriptions (
            subscription_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            email           VARCHAR(255) NOT NULL,
            source          VARCHAR(64)  NOT NULL DEFAULT 'popup',
            ip_address      VARCHAR(64),
            user_agent      TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_marketing_subscriptions_created
            ON marketing_subscriptions (created_at DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS uq_marketing_subscriptions_email
            ON marketing_subscriptions (LOWER(email));
    """)

    await _pg_pool.execute("""
        CREATE TABLE IF NOT EXISTS email_suppressions (
            suppression_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            email          VARCHAR(255) NOT NULL,
            reason         VARCHAR(200) NOT NULL DEFAULT 'unsubscribed',
            source         VARCHAR(64)  NOT NULL DEFAULT 'unsubscribe_link',
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_email_suppressions_email
            ON email_suppressions (LOWER(email));
    """)

    # Client-supplied idempotency key: a retried write returns the first event.
    await _pg_pool.execute("""
        ALTER TABLE events ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(255);
        CREATE UNIQUE INDEX IF NOT EXISTS uq_events_idempotency
            ON events (tenant_id, agent_id, idempotency_key)
            WHERE idempotency_key IS NOT NULL;
    """)

    # Per-agent hash chain (services/audit_chain.py) and the links of deleted rows.
    await _pg_pool.execute("""
        ALTER TABLE events ADD COLUMN IF NOT EXISTS chain_hash VARCHAR(64);
        CREATE TABLE IF NOT EXISTS event_tombstones (
            tenant_id   UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            agent_id    VARCHAR(255) NOT NULL,
            event_id    UUID NOT NULL,
            sequence_no BIGINT NOT NULL,
            chain_hash  VARCHAR(64) NOT NULL,
            reason      VARCHAR(50) NOT NULL,
            deleted_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (tenant_id, event_id)
        );
        CREATE INDEX IF NOT EXISTS idx_event_tombstones_agent_seq
            ON event_tombstones (tenant_id, agent_id, sequence_no DESC);
    """)

    await _ensure_fts_index()



async def init_db():
    global _pg_pool, _redis, _qdrant

    # ---------------- PostgreSQL ----------------

    _pg_pool = await asyncpg.create_pool(
        dsn=os.getenv("DATABASE_URL"),
        min_size=2,
        max_size=PG_POOL_MAX_SIZE,
    )

    logger.info("Postgres connected")
    if uses_default_db_password():
        # A literal message: nothing derived from DATABASE_URL is logged.
        logger.warning(
            "Postgres uses a well-known password. Keep its port private, and set a "
            "random POSTGRES_PASSWORD (ALTER USER ... PASSWORD, then update infra/.env); "
            "see wiki/Self-Hosting.md."
        )
    workers, assumed = uvicorn_worker_count()
    pool_warn = warn_if_pool_near_pg_limit(
        PG_POOL_MAX_SIZE, workers, assumed=assumed
    )
    if pool_warn:
        logger.warning(pool_warn)

    try:
        user_count = await _pg_pool.fetchval(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema='public'
            AND table_name='users'
            """
        )

        if user_count:
            n = await _pg_pool.fetchval("SELECT COUNT(*) FROM users")
            logger.info("Postgres users table: %s rows", n)
        else:
            logger.warning(
                "Users table missing."
            )

    except Exception as e:
        logger.warning("Could not inspect users table: %s", e)

    # -----------------------------------------------------------------
    # Schema migrations: idempotent DDL, run on every start.
    # -----------------------------------------------------------------
    # Every uvicorn worker runs init_db at the same moment. The advisory lock
    # lets one worker migrate while the others wait, instead of racing on the
    # same DDL (which crashed a worker during start-up).
    async with _pg_pool.acquire() as lock_conn:
        await lock_conn.execute("SELECT pg_advisory_lock($1)", MIGRATION_LOCK_ID)
        try:
            await _run_migrations()
        finally:
            await lock_conn.execute("SELECT pg_advisory_unlock($1)", MIGRATION_LOCK_ID)

    _redis = redis.from_url(redis_url())
    logger.info("Redis connected")

    # ---------------- Qdrant ----------------

    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333").strip()

    from services.entitlements import embeddings_enabled

    # QDRANT_URL="" opts out of Qdrant entirely (single-container quickstart).
    # Only honoured with embeddings off, where nothing reads or writes vectors.
    if not qdrant_url:
        if embeddings_enabled():
            raise RuntimeError(
                "QDRANT_URL is empty but embeddings are enabled. "
                "Set QDRANT_URL, or set EMBEDDINGS_ENABLED=false."
            )
        _qdrant = None
        logger.info("Qdrant disabled (QDRANT_URL empty, embeddings off)")
        return

    _qdrant = AsyncQdrantClient(
        url=qdrant_url,
        api_key=qdrant_api_key(),
        timeout=30,
        check_compatibility=False,
    )

    last_error = None

    for attempt in range(15):
        try:
            await _qdrant.get_collections()

            logger.info(
                "Connected to Qdrant (attempt %d)",
                attempt + 1,
            )

            break

        except Exception as e:
            last_error = e

            logger.warning(
                "Waiting for Qdrant (%d/15): %s",
                attempt + 1,
                e,
            )

            await asyncio.sleep(2)

    else:
        raise RuntimeError(
            f"Unable to connect to Qdrant after retries: {last_error}"
        )

    await _ensure_qdrant_collection()



    await _log_qdrant_version()

    logger.info("Qdrant connected")


# --------------------------------------------------------------------
# Ensure collection exists
# --------------------------------------------------------------------

async def _ensure_qdrant_collection():
    if _qdrant is None:
        raise RuntimeError("Qdrant client not initialized")

    collections = await _qdrant.get_collections()

    names = {c.name for c in collections.collections}

    if QDRANT_COLLECTION not in names:
        await _qdrant.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config=VectorParams(
                size=VECTOR_SIZE,
                distance=Distance.COSINE,
            ),
        )

        logger.info(
            "Created Qdrant collection '%s'",
            QDRANT_COLLECTION,
        )


# --------------------------------------------------------------------
# Shutdown
# --------------------------------------------------------------------

async def close_db():
    if _pg_pool:
        await _pg_pool.close()

    if _redis:
        await _redis.aclose()

    if _qdrant:
        await _qdrant.close()


# --------------------------------------------------------------------
# Accessors
# --------------------------------------------------------------------

def get_pool() -> asyncpg.Pool:
    if _pg_pool is None:
        raise RuntimeError("Postgres not initialized")
    return _pg_pool


def get_redis() -> redis.Redis:
    if _redis is None:
        raise RuntimeError("Redis not initialized")
    return _redis


def get_qdrant() -> AsyncQdrantClient:
    if _qdrant is None:
        raise RuntimeError("Qdrant not initialized")
    return _qdrant


async def _log_qdrant_version() -> None:
    try:
        import httpx

        url = os.getenv("QDRANT_URL", "http://localhost:6333").rstrip("/")
        async with httpx.AsyncClient(timeout=3.0) as client:
            key = qdrant_api_key()
            response = await client.get(f"{url}/", headers={"api-key": key} if key else None)
            if response.status_code == 200:
                version = response.json().get("version", "unknown")
                logger.info("Qdrant server version: %s", version)
    except Exception as exc:
        logger.warning("Could not fetch Qdrant version: %s", exc)


async def check_postgres() -> dict:
    try:
        await get_pool().fetchval("SELECT 1")
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def check_redis() -> dict:
    try:
        pong = await get_redis().ping()
        return {"ok": bool(pong)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def check_qdrant() -> dict:
    try:
        collections = await get_qdrant().get_collections()
        names = [collection.name for collection in collections.collections]
        return {"ok": True, "collections": names}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

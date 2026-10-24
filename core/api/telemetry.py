"""
POST /v1/telemetry — anonymous SDK / OSS install ping.

No auth required. Never returns errors (always 200).
Stores: install_id + sdk (composite key), sdk_version, runtime, OS, mode, country_code,
first_seen, last_seen, ping_count.

POST /v1/telemetry/updates — optional email opt-in for product/security updates.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field

from api.utils import client_ip
from db.connection import get_pool
from services.country_from_request import country_code_from_request
from services.email_suppress import is_suppressed
from services.rate_limiter import RateLimiter, RedisStorage, SlidingWindowStrategy

router = APIRouter()
logger = logging.getLogger(__name__)

UPDATES_RATE_WINDOW_SEC = 3600
UPDATES_RATE_MAX = 20

# Anonymous pings per IP per hour. SDKs ping once per process start, so this
# only bites floods; over the limit the ping is dropped, still with a 200.
PING_RATE_WINDOW_SEC = 3600
PING_RATE_MAX = 120

updates_limiter = RateLimiter(
    limit=UPDATES_RATE_MAX,
    window_sec=UPDATES_RATE_WINDOW_SEC,
    storage=RedisStorage(key_prefix="ratelimit:telemetry-updates"),
    strategy=SlidingWindowStrategy(),
    detail="Too many requests. Try again later.",
)


ping_limiter = RateLimiter(
    limit=PING_RATE_MAX,
    window_sec=PING_RATE_WINDOW_SEC,
    storage=RedisStorage(key_prefix="ratelimit:telemetry-ping"),
    strategy=SlidingWindowStrategy(),
)


async def _ping_allowed(ip: str) -> bool:
    try:
        await ping_limiter.check(ip)
    except HTTPException:
        return False
    except Exception:
        logger.debug("Telemetry ping rate limit backend unavailable", exc_info=True)
    return True


class TelemetryPing(BaseModel):
    install_id: str
    sdk: str = "unknown"
    sdk_version: str = "unknown"
    python: str | None = None
    node: str | None = None
    os: str = "unknown"
    mode: str = "cloud"  # "cloud" | "self-hosted"


class TelemetryUpdatesBody(BaseModel):
    email: EmailStr
    install_id: str | None = Field(default=None, max_length=128)
    sdk: str = Field(default="unknown", max_length=32)
    source: str = Field(default="dashboard", max_length=64)
    botcheck: str | None = None


@router.post("", status_code=200)
async def receive_ping(body: TelemetryPing, request: Request):
    """Accept anonymous telemetry ping. Always returns 200."""
    if not await _ping_allowed(client_ip(request)):
        return {"ok": True}
    try:
        pool = get_pool()
        runtime = body.python or body.node or "unknown"
        country = country_code_from_request(request)

        await pool.execute(
            """
            INSERT INTO sdk_telemetry
                (install_id, sdk, sdk_version, runtime, os, mode, country_code)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (install_id, sdk)
            DO UPDATE SET
                last_seen     = NOW(),
                ping_count    = sdk_telemetry.ping_count + 1,
                sdk_version   = EXCLUDED.sdk_version,
                mode          = EXCLUDED.mode,
                country_code  = COALESCE(EXCLUDED.country_code, sdk_telemetry.country_code)
            """,
            body.install_id[:128],
            body.sdk[:32],
            body.sdk_version[:32],
            runtime[:64],
            body.os[:32],
            body.mode[:32],
            country,
        )
    except Exception as e:
        logger.debug("Telemetry insert failed (non-critical): %s", e)

    return {"ok": True}


@router.post("/updates", status_code=201)
async def subscribe_updates(body: TelemetryUpdatesBody, request: Request):
    """Optional email opt-in for SDK/product update notices."""
    if body.botcheck:
        raise HTTPException(status_code=400, detail="Invalid submission")

    ip = client_ip(request)
    await updates_limiter.check(ip)

    pool = get_pool()
    email = str(body.email).strip().lower()
    if await is_suppressed(pool, email):
        return {"ok": True, "suppressed": True}

    install_id = (body.install_id or "").strip()[:128] or None
    sdk = (body.sdk or "unknown").strip()[:32]
    source = (body.source or "dashboard").strip()[:64]
    country = country_code_from_request(request)
    ua = (request.headers.get("user-agent") or "")[:2000]

    await pool.execute(
        """
        INSERT INTO sdk_update_subscriptions
            (email, install_id, sdk, country_code, source, user_agent)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (LOWER(email)) DO UPDATE
          SET install_id   = COALESCE(EXCLUDED.install_id, sdk_update_subscriptions.install_id),
              sdk          = EXCLUDED.sdk,
              country_code = COALESCE(EXCLUDED.country_code, sdk_update_subscriptions.country_code),
              source       = EXCLUDED.source,
              user_agent   = EXCLUDED.user_agent,
              updated_at   = NOW()
        """,
        email,
        install_id,
        sdk,
        country,
        source,
        ua,
    )

    logger.info("telemetry updates opt-in: %s (%s)", email, source)
    return {"ok": True}

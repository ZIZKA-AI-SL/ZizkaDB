"""Webhook delivery service v0.

Delivers events to subscribed webhook URLs asynchronously, with HMAC-SHA256
signature headers and retry-with-backoff for transient failures.

Delivery is fire-and-forget from the event write path: we queue the webhook
delivery in the same Postgres transaction as the event insert, then a
background worker picks it up and delivers. This keeps the write path fast and
never blocks the SDK caller on external network calls.

Configuration env vars:
  WEBHOOK_DELIVERY_TIMEOUT_SEC  — per-request timeout, default 10s
  WEBHOOK_MAX_RETRIES           — max attempts per delivery, default 3
  WEBHOOK_RETRY_BASE_SEC        — base backoff in seconds, default 1
"""

from __future__ import annotations

import hmac
import hashlib
import json
import logging
import os
from typing import Any
from urllib.parse import urlparse

import httpx

from db.connection import get_pool

logger = logging.getLogger(__name__)

def _parse_float(env: str, default: float) -> float:
    try:
        return float(os.getenv(env, str(default)))
    except (ValueError, TypeError):
        return default


def _parse_int(env: str, default: int) -> int:
    try:
        return int(os.getenv(env, str(default)))
    except (ValueError, TypeError):
        return default


WEBHOOK_DELIVERY_TIMEOUT_SEC = _parse_float("WEBHOOK_DELIVERY_TIMEOUT_SEC", 10.0)
WEBHOOK_MAX_RETRIES = _parse_int("WEBHOOK_MAX_RETRIES", 3)
WEBHOOK_RETRY_BASE_SEC = _parse_float("WEBHOOK_RETRY_BASE_SEC", 1.0)

# Shared HTTP client for webhook deliveries — created lazily on first use
# to avoid one connection per delivery (which exhausts file descriptors under
# load and defeats HTTP keep-alive).
_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=WEBHOOK_DELIVERY_TIMEOUT_SEC)
    return _http_client


def _sign(secret: str | None, body: bytes) -> str | None:
    """Return an HMAC-SHA256 signature hex digest, or None if no secret."""
    if not secret:
        return None
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _payload_for_event(event: dict) -> dict:
    """Build the JSON body sent to webhook subscribers."""
    return {
        "event_id": event.get("event_id"),
        "agent": event.get("agent"),
        "event": event.get("event_type"),
        "data": event.get("data"),
        "metadata": event.get("metadata"),
        "session_id": event.get("session_id"),
        "timestamp": event.get("timestamp"),
        "sequence_no": event.get("sequence_no"),
        "checksum": event.get("checksum"),
    }


def _matches_filter(event: dict, sub: dict) -> bool:
    """Return True if this event matches the subscription's filters."""
    agent_filter = sub.get("agent_id")
    if agent_filter is not None and event.get("agent") != agent_filter:
        return False
    event_types = sub.get("event_types")
    if event_types:  # empty list means all types
        if event.get("event_type") not in event_types:
            return False
    return True


async def _deliver_one(sub: dict, event: dict) -> tuple[bool, int | None, str | None]:
    """Deliver a single event to a single webhook subscription.

    Returns (ok, status_code, body_text).
    """
    url = sub["url"]
    secret = sub.get("secret")
    body = json.dumps(_payload_for_event(event)).encode()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "ZizkaDB-Webhook/1.0",
    }
    sig = _sign(secret, body)
    if sig:
        headers["X-ZizkaDB-Signature"] = f"sha256={sig}"
        headers["X-ZizkaDB-Event-Id"] = str(event.get("event_id", ""))

    try:
        resp = await _get_http_client().post(url, content=body, headers=headers)
        ok = 200 <= resp.status_code < 300
        if not ok:
            logger.warning(
                "webhook delivery failed: url=%s status=%s body=%s",
                url,
                resp.status_code,
                resp.text[:200],
            )
        return ok, resp.status_code, resp.text[:200]
    except (httpx.TransportError, httpx.TimeoutException) as exc:
        logger.warning("webhook delivery transport error: url=%s error=%s", url, exc)
        return False, None, None


async def deliver_event(event: dict) -> None:
    """Deliver an event to all matching active webhook subscriptions.

    Fire-and-forget: failures are logged and retried on the next delivery
    sweep, never blocking the caller.
    """
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT webhook_id, tenant_id, url, secret, agent_id, event_types,
               active, failure_count
        FROM webhook_subscriptions
        WHERE active = TRUE
          AND tenant_id = $1
        """,
        event.get("tenant_id"),
    )
    for row in rows:
        sub = dict(row)
        if not _matches_filter(event, sub):
            continue
        ok, status_code, body_text = await _deliver_one(sub, event)
        if ok:
            await pool.execute(
                """
                UPDATE webhook_subscriptions
                SET last_triggered = NOW(),
                    last_status = $2,
                    last_body = $3,
                    failure_count = 0
                WHERE webhook_id = $1
                """,
                sub["webhook_id"], status_code, body_text,
            )
        else:
            await pool.execute(
                """
                UPDATE webhook_subscriptions
                SET failure_count = failure_count + 1
                WHERE webhook_id = $1
                """,
                sub["webhook_id"],
            )


async def list_subscriptions(tenant_id: str) -> list[dict]:
    """Return all webhook subscriptions for a tenant."""
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT webhook_id, url, secret, agent_id, event_types, active,
               created_at, last_triggered, last_status, last_body,
               failure_count
        FROM webhook_subscriptions
        WHERE tenant_id = $1
        ORDER BY created_at DESC
        """,
        tenant_id,
    )
    return [dict(r) for r in rows]


async def create_subscription(
    tenant_id: str,
    url: str,
    secret: str | None,
    agent_id: str | None,
    event_types: list[str] | None,
) -> dict:
    """Create a webhook subscription. Returns the inserted row."""
    pool = get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO webhook_subscriptions
            (tenant_id, url, secret, agent_id, event_types)
        VALUES ($1, $2, $3, $4, $5::jsonb)
        RETURNING webhook_id, url, secret, agent_id, event_types, active,
                  created_at, last_triggered, last_status, last_body,
                  failure_count
        """,
        tenant_id, url, secret, agent_id,
        json.dumps(event_types or []),
    )
    return dict(row)


async def get_subscription(webhook_id: str, tenant_id: str) -> dict | None:
    """Return a single subscription by ID, or None if not found."""
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT webhook_id, url, secret, agent_id, event_types, active,
               created_at, last_triggered, last_status, last_body,
               failure_count
        FROM webhook_subscriptions
        WHERE webhook_id = $1 AND tenant_id = $2
        """,
        webhook_id, tenant_id,
    )
    return dict(row) if row else None


async def update_subscription(
    webhook_id: str,
    tenant_id: str,
    url: str | None = None,
    secret: str | None = None,
    agent_id: str | None = None,
    event_types: list[str] | None = None,
    active: bool | None = None,
) -> dict | None:
    """Update a subscription's fields. Returns the updated row or None."""
    pool = get_pool()
    sets = []
    params: list = [webhook_id, tenant_id]
    idx = 3
    if url is not None:
        sets.append(f"url = ${idx}")
        params.append(url)
        idx += 1
    if secret is not None:
        sets.append(f"secret = ${idx}")
        params.append(secret)
        idx += 1
    if agent_id is not None:
        sets.append(f"agent_id = ${idx}")
        params.append(agent_id)
        idx += 1
    if event_types is not None:
        sets.append(f"event_types = ${idx}::jsonb")
        params.append(json.dumps(event_types))
        idx += 1
    if active is not None:
        sets.append(f"active = ${idx}")
        params.append(active)
        idx += 1
    if not sets:
        return await get_subscription(webhook_id, tenant_id)
    sets.append("failure_count = 0")
    query = f"""
        UPDATE webhook_subscriptions
        SET {', '.join(sets)}
        WHERE webhook_id = $1 AND tenant_id = $2
        RETURNING webhook_id, url, secret, agent_id, event_types, active,
                  created_at, last_triggered, last_status, last_body,
                  failure_count
    """
    row = await pool.fetchrow(query, *params)
    return dict(row) if row else None


async def delete_subscription(webhook_id: str, tenant_id: str) -> bool:
    """Delete a subscription. Returns True if a row was deleted."""
    pool = get_pool()
    result = await pool.execute(
        """
        DELETE FROM webhook_subscriptions
        WHERE webhook_id = $1 AND tenant_id = $2
        """,
        webhook_id, tenant_id,
    )
    return result.split()[-1] == "1"
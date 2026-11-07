"""Webhook subscriptions API.

Create, list, update, and delete outbound webhook subscriptions that receive
every event matching an optional agent/event-type filter.

Routes:
  GET    /v1/webhooks               — list subscriptions for the tenant
  POST   /v1/webhooks               — create a subscription
  GET    /v1/webhooks/{webhook_id}  — get one subscription
  PATCH  /v1/webhooks/{webhook_id}  — update a subscription
  DELETE /v1/webhooks/{webhook_id}  — delete a subscription
  POST   /v1/webhooks/{webhook_id}/test — send a test ping

All routes require a dashboard session (JWT): webhook management is a
dashboard-only action, never callable with a scoped API key.
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field, HttpUrl
from typing import Any

from api.deps import require_dashboard_session
from db.connection import get_pool
from services.exceptions import not_found, bad_request, conflict
from services.webhooks import (
    list_subscriptions,
    create_subscription,
    get_subscription,
    update_subscription,
    delete_subscription,
    _deliver_one,
)

router = APIRouter()

log = logging.getLogger(__name__)


class WebhookSubscription(BaseModel):
    webhook_id: UUID
    url: str
    secret: str | None = None
    agent_id: str | None = None
    event_types: list[str]
    active: bool
    created_at: datetime
    last_triggered: datetime | None = None
    last_status: int | None = None
    last_body: str | None = None
    failure_count: int


class CreateWebhookBody(BaseModel):
    url: HttpUrl
    secret: str | None = Field(
        default=None,
        max_length=255,
        description="Optional HMAC secret. If set, every delivery includes an "
                    "X-ZizkaDB-Signature header with the SHA-256 HMAC of the body.",
    )
    agent_id: str | None = Field(
        default=None,
        max_length=255,
        description="Optional agent filter. Only events for this agent are sent. "
                    "Unset = all agents.",
    )
    event_types: list[str] | None = Field(
        default=None,
        description="Optional event-type filter. Empty list = all event types.",
    )
    active: bool = True


class UpdateWebhookBody(BaseModel):
    url: HttpUrl | None = None
    secret: str | None = None
    agent_id: str | None = Field(default=None, max_length=255)
    event_types: list[str] | None = None
    active: bool | None = None


class WebhookResponse(BaseModel):
    webhook_id: UUID
    url: str
    # secret is never returned to the client (only set at creation time)
    agent_id: str | None = None
    event_types: list[str]
    active: bool
    created_at: datetime
    last_triggered: datetime | None = None
    last_status: int | None = None
    last_body: str | None = None
    failure_count: int


def _row_to_response(row: dict) -> WebhookResponse:
    """Convert a database row to a WebhookResponse, handling type conversions.

    asyncpg returns UUID as uuid.UUID, datetime as datetime, and JSONB as
    a Python list/dict. We convert all of these to the types expected by
    the response model.
    """
    event_types = row.get("event_types")
    if isinstance(event_types, str):
        import json
        event_types = json.loads(event_types)
    elif event_types is None:
        event_types = []

    return WebhookResponse(
        webhook_id=row["webhook_id"],
        url=row["url"],
        agent_id=row["agent_id"],
        event_types=event_types,
        active=row["active"],
        created_at=row["created_at"],
        last_triggered=row["last_triggered"],
        last_status=row["last_status"],
        last_body=row["last_body"],
        failure_count=row["failure_count"],
    )


@router.get("")
async def list_webhooks(
    session: dict = Depends(require_dashboard_session),
) -> list[WebhookResponse]:
    """List all webhook subscriptions for the current tenant."""
    rows = await list_subscriptions(session["tenant_id"])
    return [WebhookResponse(**_row_to_response(r)) for r in rows]


@router.post("", status_code=201)
async def create_webhook(
    body: CreateWebhookBody,
    session: dict = Depends(require_dashboard_session),
) -> WebhookResponse:
    """Create a new webhook subscription."""
    row = await create_subscription(
        tenant_id=session["tenant_id"],
        url=str(body.url),
        secret=body.secret,
        agent_id=body.agent_id,
        event_types=body.event_types,
    )
    return WebhookResponse(**_row_to_response(row))


@router.get("/{webhook_id}")
async def get_webhook(
    webhook_id: UUID = Path(...),
    session: dict = Depends(require_dashboard_session),
) -> WebhookResponse:
    """Get a single webhook subscription by ID."""
    row = await get_subscription(webhook_id, session["tenant_id"])
    if not row:
        raise not_found("Webhook subscription not found")
    return WebhookResponse(**_row_to_response(row))


@router.patch("/{webhook_id}")
async def update_webhook(
    webhook_id: UUID = Path(...),
    body: UpdateWebhookBody = ...,
    session: dict = Depends(require_dashboard_session),
) -> WebhookResponse:
    """Update a webhook subscription."""
    row = await update_subscription(
        webhook_id,
        session["tenant_id"],
        url=str(body.url) if body.url is not None else None,
        secret=body.secret,
        agent_id=body.agent_id,
        event_types=body.event_types,
        active=body.active,
    )
    if not row:
        raise not_found("Webhook subscription not found")
    return WebhookResponse(**_row_to_response(row))


@router.delete("/{webhook_id}")
async def delete_webhook(
    webhook_id: UUID = Path(...),
    session: dict = Depends(require_dashboard_session),
) -> dict:
    """Delete a webhook subscription."""
    deleted = await delete_subscription(webhook_id, session["tenant_id"])
    if not deleted:
        raise not_found("Webhook subscription not found")
    return {"deleted": True, "webhook_id": str(webhook_id)}


@router.post("/{webhook_id}/test")
async def test_webhook(
    webhook_id: UUID = Path(...),
    session: dict = Depends(require_dashboard_session),
) -> dict:
    """Send a test ping to a webhook subscription. Returns delivery status."""
    row = await get_subscription(webhook_id, session["tenant_id"])
    if not row:
        raise not_found("Webhook subscription not found")

    test_event = {
        "event_id": "test-" + str(webhook_id),
        "agent": row.get("agent_id") or "webhook-test",
        "event_type": "webhook.test",
        "data": {"message": "webhook test ping"},
        "metadata": {},
        "session_id": None,
        "timestamp": None,
        "sequence_no": None,
        "checksum": None,
        "tenant_id": session["tenant_id"],
    }

    ok = False
    status_code = None
    body_text = None
    try:
        ok, status_code, body_text = await _deliver_one(dict(row), test_event)
    except Exception as exc:
        log.warning("webhook test delivery failed: url=%s error=%s", row["url"], exc)
        ok = False

    pool = get_pool()
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
            webhook_id, status_code, body_text,
        )
        return {"delivered": True, "webhook_id": str(webhook_id)}
    else:
        await pool.execute(
            """
            UPDATE webhook_subscriptions
            SET failure_count = failure_count + 1
            WHERE webhook_id = $1
            """,
            webhook_id,
        )
        return {"delivered": False, "webhook_id": str(webhook_id)}
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
    webhook_id: str
    url: str
    secret: str | None = None
    agent_id: str | None = None
    event_types: list[str]
    active: bool
    created_at: str
    last_triggered: str | None = None
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
    webhook_id: str
    url: str
    secret: str | None = None
    agent_id: str | None = None
    event_types: list[str]
    active: bool
    created_at: str
    last_triggered: str | None = None
    last_status: int | None = None
    last_body: str | None = None
    failure_count: int


@router.get("")
async def list_webhooks(
    session: dict = Depends(require_dashboard_session),
) -> list[WebhookResponse]:
    """List all webhook subscriptions for the current tenant."""
    rows = await list_subscriptions(session["tenant_id"])
    return [WebhookResponse(**r) for r in rows]


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
    return WebhookResponse(**row)


@router.get("/{webhook_id}")
async def get_webhook(
    webhook_id: str = Path(...),
    session: dict = Depends(require_dashboard_session),
) -> WebhookResponse:
    """Get a single webhook subscription by ID."""
    row = await get_subscription(webhook_id, session["tenant_id"])
    if not row:
        raise not_found("Webhook subscription not found")
    return WebhookResponse(**row)


@router.patch("/{webhook_id}")
async def update_webhook(
    webhook_id: str = Path(...),
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
    return WebhookResponse(**row)


@router.delete("/{webhook_id}")
async def delete_webhook(
    webhook_id: str = Path(...),
    session: dict = Depends(require_dashboard_session),
) -> dict:
    """Delete a webhook subscription."""
    deleted = await delete_subscription(webhook_id, session["tenant_id"])
    if not deleted:
        raise not_found("Webhook subscription not found")
    return {"deleted": True, "webhook_id": webhook_id}


@router.post("/{webhook_id}/test")
async def test_webhook(
    webhook_id: str = Path(...),
    session: dict = Depends(require_dashboard_session),
) -> dict:
    """Send a test ping to a webhook subscription. Returns delivery status."""
    row = await get_subscription(webhook_id, session["tenant_id"])
    if not row:
        raise not_found("Webhook subscription not found")

    test_event = {
        "event_id": "test-" + webhook_id,
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
        return {"delivered": True, "webhook_id": webhook_id}
    else:
        await pool.execute(
            """
            UPDATE webhook_subscriptions
            SET failure_count = failure_count + 1
            WHERE webhook_id = $1
            """,
            webhook_id,
        )
        return {"delivered": False, "webhook_id": webhook_id}
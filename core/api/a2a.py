from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.deps import get_tenant
from db.connection import get_pool
from services.entitlements import is_self_hosted_deployment
from services.event_write import write_event
from services.exceptions import bad_request, forbidden, not_found


router = APIRouter()


class A2AMessageRequest(BaseModel):
    recipient_agent: str = Field(..., min_length=1, max_length=255)
    message: str = Field(..., min_length=1, max_length=100_000)
    session_id: str | None = Field(default=None, max_length=255)
    correlation_id: str | None = Field(default=None, max_length=255)
    metadata: dict[str, Any] | None = None


@router.post("/messages", status_code=201)
async def send_message(
    body: A2AMessageRequest,
    tenant: dict = Depends(get_tenant),
):
    """
    Send a message from the authenticated agent to another
    agent belonging to the same tenant.

    The sender is derived exclusively from the authenticated
    agent-scoped API key and cannot be supplied by the client.
    """

    # Multi-agent feature: managed cloud only. Self-hosted is single-agent.
    if is_self_hosted_deployment():
        raise forbidden(
            detail=(
                "Agent-to-agent communication is available on ZizkaDB Cloud "
                "(https://db.zizka.ai). Self-hosted deployments run a single agent."
            )
        )

    tenant_id = tenant["tenant_id"]
    sender_agent = tenant.get("agent_id")

    # A2A requires an agent identity. Tenant-wide credentials must
    # not be allowed to impersonate arbitrary agents.
    if not sender_agent:
        raise forbidden(
            detail=(
                "Agent-to-agent communication requires an "
                "agent-scoped API key"
            )
        )

    recipient_agent = body.recipient_agent

    if sender_agent == recipient_agent:
        raise bad_request(
            "Sender and recipient must be different agents"
        )

    pool = get_pool()

    # Recipient must already exist in the authenticated tenant.
    recipient_exists = await pool.fetchval(
        """
        SELECT 1
        FROM agents
        WHERE tenant_id = $1
          AND agent_id = $2
        LIMIT 1
        """,
        tenant_id,
        recipient_agent,
    )

    if not recipient_exists:
        raise not_found(
            f"Recipient agent '{recipient_agent}' not found"
        )

    message_id = str(uuid4())

    event_data = {
        "message_id": message_id,
        "sender_agent": sender_agent,
        "recipient_agent": recipient_agent,
        "message": body.message,
        "direction": "request",
        "correlation_id": body.correlation_id,
    }

    result = await write_event(
        tenant_id=tenant_id,
        agent=sender_agent,
        event="a2a.message",
        data=event_data,
        session_id=body.session_id,
        metadata=body.metadata,
    )

    return {
        "message_id": message_id,
        "event_id": result["event_id"],
        "sender_agent": sender_agent,
        "recipient_agent": recipient_agent,
        "session_id": body.session_id,
        "correlation_id": body.correlation_id,
        "timestamp": result["timestamp"],
        "sequence_no": result["sequence_no"],
        "checksum": result["checksum"],
        "indexed": result["indexed"],
    }
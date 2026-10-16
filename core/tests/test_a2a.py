from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.a2a import A2AMessageRequest, send_message


TENANT = {
    "tenant_id": "tenant-1",
    "agent_id": "agent-a",
}

TENANT_WIDE_KEY = {
    "tenant_id": "tenant-1",
}


@pytest.fixture
def mock_pool(monkeypatch):
    pool = AsyncMock()

    monkeypatch.setattr(
        "api.a2a.get_pool",
        lambda: pool,
    )

    return pool


@pytest.fixture
def mock_write_event(monkeypatch):
    write_event = AsyncMock(
        return_value={
            "event_id": "event-123",
            "timestamp": "2026-08-13T12:00:00+00:00",
            "sequence_no": 7,
            "checksum": "abc123",
            "indexed": False,
        }
    )

    monkeypatch.setattr(
        "api.a2a.write_event",
        write_event,
    )

    return write_event


@pytest.mark.asyncio
async def test_send_a2a_message_success(
    mock_pool,
    mock_write_event,
):
    mock_pool.fetchval.return_value = 1

    body = A2AMessageRequest(
        recipient_agent="agent-b",
        message="Hello agent B",
        session_id="session-1",
        correlation_id="corr-1",
    )

    result = await send_message(
        body,
        tenant=TENANT,
    )

    assert result["event_id"] == "event-123"
    assert result["sender_agent"] == "agent-a"
    assert result["recipient_agent"] == "agent-b"
    assert result["session_id"] == "session-1"
    assert result["correlation_id"] == "corr-1"
    assert result["sequence_no"] == 7
    assert result["indexed"] is False

    mock_write_event.assert_awaited_once()

    kwargs = mock_write_event.await_args.kwargs

    assert kwargs["tenant_id"] == "tenant-1"
    assert kwargs["agent"] == "agent-a"
    assert kwargs["event"] == "a2a.message"
    assert kwargs["session_id"] == "session-1"

    data = kwargs["data"]

    assert data["sender_agent"] == "agent-a"
    assert data["recipient_agent"] == "agent-b"
    assert data["message"] == "Hello agent B"
    assert data["direction"] == "request"
    assert data["correlation_id"] == "corr-1"
    assert data["message_id"]


@pytest.mark.asyncio
async def test_sender_is_derived_from_authenticated_agent(
    mock_pool,
    mock_write_event,
):
    mock_pool.fetchval.return_value = 1

    body = A2AMessageRequest(
        recipient_agent="agent-b",
        message="Do not trust client sender",
    )

    await send_message(
        body,
        tenant={
            "tenant_id": "tenant-1",
            "agent_id": "agent-a",
        },
    )

    kwargs = mock_write_event.await_args.kwargs

    assert kwargs["agent"] == "agent-a"
    assert kwargs["data"]["sender_agent"] == "agent-a"


def test_a2a_request_model_has_no_sender_field():
    """Clients cannot declare a sender — it is not part of the request schema."""
    assert "sender_agent" not in A2AMessageRequest.model_fields
    assert "agent_id" not in A2AMessageRequest.model_fields


@pytest.mark.asyncio
async def test_extra_sender_fields_cannot_spoof_authenticated_agent(
    mock_pool,
    mock_write_event,
):
    """Extra JSON like sender_agent / agent_id must not override the scoped key."""
    mock_pool.fetchval.return_value = 1

    body = A2AMessageRequest.model_validate(
        {
            "recipient_agent": "agent-b",
            "message": "spoof attempt",
            "sender_agent": "agent-evil",
            "agent_id": "agent-evil",
            "from": "agent-evil",
        }
    )

    assert "sender_agent" not in body.model_fields_set
    assert "agent_id" not in body.model_fields_set

    result = await send_message(body, tenant=TENANT)

    assert result["sender_agent"] == "agent-a"
    kwargs = mock_write_event.await_args.kwargs
    assert kwargs["agent"] == "agent-a"
    assert kwargs["data"]["sender_agent"] == "agent-a"


@pytest.mark.asyncio
async def test_metadata_sender_does_not_override_authenticated_agent(
    mock_pool,
    mock_write_event,
):
    mock_pool.fetchval.return_value = 1

    body = A2AMessageRequest(
        recipient_agent="agent-b",
        message="Hello",
        metadata={"sender_agent": "agent-evil", "agent_id": "agent-evil"},
    )

    result = await send_message(body, tenant=TENANT)

    assert result["sender_agent"] == "agent-a"
    kwargs = mock_write_event.await_args.kwargs
    assert kwargs["agent"] == "agent-a"
    assert kwargs["data"]["sender_agent"] == "agent-a"
    assert kwargs["metadata"]["sender_agent"] == "agent-evil"


@pytest.mark.asyncio
async def test_tenant_wide_key_cannot_send_a2a_message(
    mock_pool,
    mock_write_event,
):
    body = A2AMessageRequest(
        recipient_agent="agent-b",
        message="Hello",
    )

    with pytest.raises(HTTPException) as exc:
        await send_message(
            body,
            tenant=TENANT_WIDE_KEY,
        )

    assert exc.value.status_code == 403
    assert "agent-scoped" in str(exc.value.detail).lower()
    mock_pool.fetchval.assert_not_called()
    mock_write_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_agent_id_cannot_send_a2a_message(
    mock_pool,
    mock_write_event,
):
    body = A2AMessageRequest(
        recipient_agent="agent-b",
        message="Hello",
    )

    with pytest.raises(HTTPException) as exc:
        await send_message(
            body,
            tenant={"tenant_id": "tenant-1", "agent_id": ""},
        )

    assert exc.value.status_code == 403
    mock_pool.fetchval.assert_not_called()
    mock_write_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_dashboard_jwt_without_agent_cannot_send_a2a_message(
    mock_pool,
    mock_write_event,
):
    body = A2AMessageRequest(
        recipient_agent="agent-b",
        message="Hello",
    )

    with pytest.raises(HTTPException) as exc:
        await send_message(
            body,
            tenant={"tenant_id": "tenant-1", "user_id": "user-1"},
        )

    assert exc.value.status_code == 403
    mock_pool.fetchval.assert_not_called()
    mock_write_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_recipient_must_exist(mock_pool, mock_write_event):
    mock_pool.fetchval.return_value = None

    body = A2AMessageRequest(
        recipient_agent="agent-does-not-exist",
        message="Hello",
    )

    with pytest.raises(HTTPException) as exc:
        await send_message(
            body,
            tenant=TENANT,
        )

    assert exc.value.status_code == 404
    assert "agent-does-not-exist" in str(exc.value.detail)
    mock_write_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_cross_tenant_recipient_is_rejected(mock_pool):
    # The recipient lookup is explicitly constrained by tenant_id.
    mock_pool.fetchval.return_value = None

    body = A2AMessageRequest(
        recipient_agent="agent-other-tenant",
        message="Hello",
    )

    with pytest.raises(HTTPException) as exc:
        await send_message(
            body,
            tenant=TENANT,
        )

    assert exc.value.status_code == 404

    sql, tenant_id, recipient = (
        mock_pool.fetchval.await_args.args
    )

    assert "tenant_id = $1" in sql
    assert "agent_id = $2" in sql
    assert tenant_id == "tenant-1"
    assert recipient == "agent-other-tenant"
    mock_pool.fetchval.assert_awaited_once()


@pytest.mark.asyncio
async def test_recipient_lookup_ignores_tenant_id_in_payload(
    mock_pool,
    mock_write_event,
):
    """A client-supplied tenant_id in metadata must not change the SQL bind."""
    mock_pool.fetchval.return_value = None

    body = A2AMessageRequest(
        recipient_agent="agent-other-tenant",
        message="Hello",
        metadata={"tenant_id": "tenant-other"},
    )

    with pytest.raises(HTTPException) as exc:
        await send_message(
            body,
            tenant=TENANT,
        )

    assert exc.value.status_code == 404

    sql, tenant_id, recipient = mock_pool.fetchval.await_args.args

    assert "tenant_id = $1" in sql
    assert tenant_id == "tenant-1"
    assert tenant_id != "tenant-other"
    assert recipient == "agent-other-tenant"
    mock_write_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_cannot_send_message_to_itself(
    mock_pool,
):
    body = A2AMessageRequest(
        recipient_agent="agent-a",
        message="Self message",
    )

    with pytest.raises(HTTPException) as exc:
        await send_message(
            body,
            tenant=TENANT,
        )

    assert exc.value.status_code == 400

    mock_pool.fetchval.assert_not_called()


@pytest.mark.asyncio
async def test_message_metadata_is_preserved(
    mock_pool,
    mock_write_event,
):
    mock_pool.fetchval.return_value = 1

    metadata = {
        "priority": "high",
        "source": "test",
    }

    body = A2AMessageRequest(
        recipient_agent="agent-b",
        message="Important message",
        metadata=metadata,
    )

    await send_message(
        body,
        tenant=TENANT,
    )

    kwargs = mock_write_event.await_args.kwargs

    assert kwargs["metadata"] == metadata


@pytest.mark.asyncio
async def test_message_without_session_or_correlation_is_allowed(
    mock_pool,
    mock_write_event,
):
    mock_pool.fetchval.return_value = 1

    body = A2AMessageRequest(
        recipient_agent="agent-b",
        message="Simple message",
    )

    result = await send_message(
        body,
        tenant=TENANT,
    )

    assert result["session_id"] is None
    assert result["correlation_id"] is None

    kwargs = mock_write_event.await_args.kwargs

    assert kwargs["session_id"] is None
    assert kwargs["data"]["correlation_id"] is None


@pytest.mark.asyncio
async def test_write_event_failure_propagates(
    mock_pool,
    monkeypatch,
):
    mock_pool.fetchval.return_value = 1

    write_event = AsyncMock(
        side_effect=RuntimeError("database unavailable")
    )

    monkeypatch.setattr(
        "api.a2a.write_event",
        write_event,
    )

    body = A2AMessageRequest(
        recipient_agent="agent-b",
        message="Hello",
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        await send_message(
            body,
            tenant=TENANT,
        )

@pytest.mark.asyncio
async def test_a2a_is_not_available_on_self_hosted(monkeypatch, mock_pool, mock_write_event):
    """Agent-to-agent messaging is a ZizkaDB Cloud feature; self-hosted is single-agent."""
    monkeypatch.setenv("DEPLOYMENT_MODE", "self_hosted")

    with pytest.raises(HTTPException) as exc:
        await send_message(
            A2AMessageRequest(recipient_agent="other", message="hi"),
            tenant={"tenant_id": "t-1", "key_id": "k-1", "agent_id": "mine"},
        )

    assert exc.value.status_code == 403
    assert "ZizkaDB Cloud" in str(exc.value.detail)
    mock_write_event.assert_not_called()

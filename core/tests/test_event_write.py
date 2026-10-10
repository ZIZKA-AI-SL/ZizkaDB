import datetime
from unittest.mock import AsyncMock, MagicMock
import pytest

from services.checksum import compute_event_checksum_v2
from services.event_write import _pgvector_literal, write_event


def test_pgvector_literal_formats_as_bracketed_csv():
    """asyncpg has no codec for pgvector's `vector` type — a raw list param
    fails with 'expected str, got list'. This must produce pgvector's text
    input format so `$1::vector` can parse it."""
    assert _pgvector_literal([0.1, -0.2, 3.0]) == "[0.1,-0.2,3.0]"
    assert _pgvector_literal([]) == "[]"


ROW_TIMESTAMP = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
ROW = {
    "event_id": "12345678-1234-1234-1234-123456789abc",
    "timestamp": ROW_TIMESTAMP,
    "sequence_no": 7,
}


@pytest.fixture
def mock_conn():
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=AsyncMock())
    conn.transaction.return_value.__aenter__ = AsyncMock(return_value=None)
    conn.transaction.return_value.__aexit__ = AsyncMock(return_value=None)
    conn.fetchrow.return_value = ROW
    return conn


@pytest.fixture
def mock_pool(monkeypatch, mock_conn):
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=AsyncMock())
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    monkeypatch.setattr(
        "services.event_write.get_pool",
        lambda: pool,
    )

    return pool


@pytest.fixture
def mock_qdrant(monkeypatch):
    qdrant = AsyncMock()

    monkeypatch.setattr(
        "services.event_write.get_qdrant",
        lambda: qdrant,
    )

    return qdrant


@pytest.mark.asyncio
async def test_write_event_success(
    monkeypatch,
    mock_pool,
    mock_conn,
    mock_qdrant,
):
    monkeypatch.setattr("services.event_write.embeddings_enabled", lambda: True)
    monkeypatch.setenv("EMBED_SYNC", "true")
    monkeypatch.setattr(
        "services.event_write.event_to_text",
        lambda event, data: "hello world",
    )

    monkeypatch.setattr(
        "services.event_write.generate_embedding",
        AsyncMock(return_value=[0.1] * 1536),
    )

    result = await write_event(
        tenant_id="tenant1",
        agent="agent1",
        event="message",
        data={"text": "hello"},
    )

    assert result["event_id"] == "12345678-1234-1234-1234-123456789abc"
    assert result["sequence_no"] == 7
    assert result["indexed"] is True
    assert result["index_status"] == "indexed"

    # Insert and checksum UPDATE happen on the acquired connection.
    mock_conn.fetchrow.assert_awaited_once()
    mock_conn.execute.assert_awaited()
    checksum_sql = mock_conn.execute.await_args_list[0].args[0]
    assert "UPDATE events SET checksum" in checksum_sql
    assert mock_pool.execute.await_count >= 3
    mock_qdrant.upsert.assert_awaited_once()


@pytest.mark.asyncio
async def test_write_event_stores_v2_checksum_over_audit_fields(
    monkeypatch,
    mock_pool,
    mock_conn,
    mock_qdrant,
):
    """The returned checksum must cover every immutable audit field."""
    monkeypatch.setattr("services.event_write.embeddings_enabled", lambda: False)
    # Parent-existence check: the mock parent belongs to the same tenant.
    mock_pool.fetchval = AsyncMock(return_value="tenant1")

    result = await write_event(
        tenant_id="tenant1",
        agent="agent1",
        event="message",
        data={"text": "hello"},
        parent_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        session_id="sess-1",
        metadata={"trace": "t-1"},
    )

    expected = compute_event_checksum_v2(
        tenant_id="tenant1",
        agent_id="agent1",
        event_type="message",
        data={"text": "hello"},
        parent_event_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        session_id="sess-1",
        timestamp=ROW_TIMESTAMP,
        sequence_no=7,
        metadata={"trace": "t-1"},
    )
    assert result["checksum"] == expected

    # The UPDATE must persist exactly that checksum against the new event.
    update_args = mock_conn.execute.await_args_list[0].args
    assert update_args[1] == expected
    assert str(update_args[2]) == ROW["event_id"]


@pytest.mark.asyncio
async def test_write_event_pending_when_async_embed(
    monkeypatch,
    mock_pool,
    mock_conn,
    mock_qdrant,
):
    monkeypatch.setattr("services.event_write.embeddings_enabled", lambda: True)
    monkeypatch.delenv("EMBED_SYNC", raising=False)
    monkeypatch.setattr(
        "services.event_write.event_to_text",
        lambda event, data: "hello world",
    )

    result = await write_event(
        tenant_id="tenant1",
        agent="agent1",
        event="message",
        data={"text": "hello"},
    )

    assert result["indexed"] is False
    assert result["index_status"] == "pending"
    mock_qdrant.upsert.assert_not_called()


@pytest.mark.asyncio
async def test_write_event_without_embedding(
    monkeypatch,
    mock_pool,
    mock_conn,
    mock_qdrant,
):
    monkeypatch.setattr(
        "services.event_write.event_to_text",
        lambda event, data: "hello",
    )

    monkeypatch.setattr(
        "services.event_write.generate_embedding",
        AsyncMock(return_value=None),
    )

    result = await write_event(
        tenant_id="tenant",
        agent="agent",
        event="message",
        data={},
    )

    mock_qdrant.upsert.assert_not_called()
    assert result["indexed"] is False


@pytest.mark.asyncio
async def test_embedding_failure_does_not_fail_request(
    monkeypatch,
    mock_pool,
    mock_conn,
    mock_qdrant,
):
    monkeypatch.setattr("services.event_write.embeddings_enabled", lambda: True)
    monkeypatch.setenv("EMBED_SYNC", "true")
    monkeypatch.setattr(
        "services.event_write.event_to_text",
        lambda event, data: "hello",
    )

    async def fail(*args, **kwargs):
        raise RuntimeError("embedding failed")

    monkeypatch.setattr(
        "services.event_write.generate_embedding",
        fail,
    )

    result = await write_event(
        tenant_id="tenant",
        agent="agent",
        event="message",
        data={},
    )

    assert result["event_id"] == "12345678-1234-1234-1234-123456789abc"
    assert result["indexed"] is False
    assert result["index_status"] == "failed"
    mock_qdrant.upsert.assert_not_called()


@pytest.mark.asyncio
async def test_usage_meter_failure_does_not_fail_request(
    monkeypatch,
    mock_pool,
    mock_conn,
    mock_qdrant,
):
    monkeypatch.setattr("services.event_write.embeddings_enabled", lambda: False)

    async def execute(sql, *args, **kwargs):
        if "usage_daily" in sql:
            raise RuntimeError("usage table failed")
        return "OK"

    mock_pool.execute = AsyncMock(side_effect=execute)

    result = await write_event(
        tenant_id="tenant",
        agent="agent",
        event="message",
        data={},
    )

    assert result["event_id"] == ROW["event_id"]
    assert result["index_status"] == "skipped"

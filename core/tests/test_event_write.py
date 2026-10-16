import datetime
from unittest.mock import AsyncMock
import pytest

from services.checksum import compute_event_checksum_v2
from services.event_write import _pgvector_literal, write_event


def test_pgvector_literal_formats_as_bracketed_csv():
    """asyncpg has no codec for pgvector's `vector` type — a raw list param
    fails with 'expected str, got list'. This must produce pgvector's text
    input format so `$1::vector` can parse it."""
    assert _pgvector_literal([0.1, -0.2, 3.0]) == "[0.1,-0.2,3.0]"
    assert _pgvector_literal([]) == "[]"


def _db_row(row: dict) -> dict:
    """One mocked row that answers both the canonicalising SELECT and the
    INSERT ... RETURNING in write_event."""
    return {
        "tenant_id": "tenant1",
        "agent_id": "agent1",
        "event_type": "message",
        "data": "{}",
        "parent_event_id": None,
        "session_id": None,
        "metadata": None,
        "checksum": None,
        **row,
    }


@pytest.fixture
def mock_pool(monkeypatch):
    pool = AsyncMock()

    row = {
        "event_id": "12345678-1234-1234-1234-123456789abc",
        "timestamp": datetime.datetime(
            2026,
            1,
            1,
            tzinfo=datetime.timezone.utc,
        ),
        "sequence_no": 7,
    }

    pool.fetchrow.return_value = _db_row(row)

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

    assert mock_pool.execute.await_count >= 3
    mock_qdrant.upsert.assert_awaited_once()


@pytest.mark.asyncio
async def test_write_event_pending_when_async_embed(
    monkeypatch,
    mock_pool,
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
):
    pool = AsyncMock()

    row = {
        "event_id": "abc",
        "timestamp": datetime.datetime.now(datetime.timezone.utc),
        "sequence_no": 1,
    }

    pool.fetchrow.return_value = _db_row(row)

    async def execute(sql, *args, **kwargs):
        if "usage_daily" in sql:
            raise RuntimeError("usage table failed")

    pool.execute.side_effect = execute

    monkeypatch.setattr(
        "services.event_write.get_pool",
        lambda: pool,
    )

    monkeypatch.setattr(
        "services.event_write.get_qdrant",
        lambda: AsyncMock(),
    )

    monkeypatch.setattr(
        "services.event_write.event_to_text",
        lambda e, d: "hello",
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

    assert result["event_id"] == "abc"


@pytest.mark.asyncio
async def test_write_event_inserts_v2_checksum_of_canonical_values(
    monkeypatch,
    mock_pool,
    mock_qdrant,
):
    """The checksum hashes the values Postgres canonicalised (what the verifier
    reads back) and is written by the INSERT itself: one row write per event."""
    monkeypatch.setattr("services.event_write.embeddings_enabled", lambda: False)
    canonical = mock_pool.fetchrow.return_value
    canonical.update(
        data='{"text": "hello"}',
        parent_event_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        metadata='{"trace": "t-1"}',
    )
    mock_pool.fetchval = AsyncMock(return_value="tenant1")

    result = await write_event(
        tenant_id="tenant1",
        agent="agent1",
        event="message",
        data={"text": "hello"},
        parent_id="AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE",
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
        timestamp=canonical["timestamp"],
        sequence_no=7,
        metadata={"trace": "t-1"},
    )
    assert result["checksum"] == expected

    select_call, insert_call = mock_pool.fetchrow.await_args_list
    assert "nextval" in select_call.args[0]
    insert_sql, *insert_args = insert_call.args
    assert "INSERT INTO events" in insert_sql
    assert insert_args[8:] == [7, canonical["timestamp"], expected]
    assert not any(
        "UPDATE events SET checksum" in c.args[0] for c in mock_pool.execute.await_args_list
    )

import datetime
from unittest.mock import AsyncMock, MagicMock
import pytest
from fastapi import HTTPException

from services.event_write import _pgvector_literal, write_event


def _attach_conn(pool):
    """Give ``pool`` an ``acquire()`` context yielding a transactional conn.

    Returns the conn so tests can inspect the statements that run inside the
    write transaction (parent check, agent upsert, event insert).
    """
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=AsyncMock())
    conn.transaction.return_value.__aenter__ = AsyncMock(return_value=None)
    conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=AsyncMock())
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return conn


def test_pgvector_literal_formats_as_bracketed_csv():
    """asyncpg has no codec for pgvector's `vector` type — a raw list param
    fails with 'expected str, got list'. This must produce pgvector's text
    input format so `$1::vector` can parse it."""
    assert _pgvector_literal([0.1, -0.2, 3.0]) == "[0.1,-0.2,3.0]"
    assert _pgvector_literal([]) == "[]"


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

    conn = _attach_conn(pool)
    conn.fetchrow.return_value = row
    pool.conn = conn

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

    # embedding UPDATE + usage_daily meter run on the pool, outside the write txn
    assert mock_pool.execute.await_count >= 2
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

    _attach_conn(pool).fetchrow.return_value = row

    async def execute(*args, **kwargs):
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
@pytest.mark.parametrize(
    "kwargs",
    [
        {"data": {"x": float("nan")}},
        {"data": {"x": float("inf")}},
        {"data": {"nested": [1, {"y": float("-inf")}]}},
        {"data": {"x": "a\x00b"}},
        {"data": {"a\x00b": 1}},
        {"data": {"x": "\ud800"}},
        {"event": "a\x00b", "data": {}},
        {"agent": "a\x00b", "data": {}},
        {"session_id": "a\x00b", "data": {}},
        {"metadata": {"k": "a\x00b"}, "data": {}},
        {"parent_id": "not-a-uuid", "data": {}},
    ],
)
async def test_unstorable_input_is_rejected_before_any_db_write(mock_pool, kwargs):
    """Input Postgres cannot store must be a 400 up front — never a 500 raised
    after the agent counter was already incremented."""
    params = {"tenant_id": "tenant1", "agent": "agent1", "event": "message", "data": {}}
    params.update(kwargs)

    with pytest.raises(HTTPException) as exc:
        await write_event(**params)

    assert exc.value.status_code == 400
    mock_pool.acquire.assert_not_called()
    mock_pool.conn.execute.assert_not_awaited()
    mock_pool.conn.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_upsert_and_event_insert_share_one_transaction(mock_pool):
    await write_event(
        tenant_id="tenant1",
        agent="agent1",
        event="message",
        data={"text": "hi"},
    )

    conn = mock_pool.conn
    conn.transaction.assert_called_once()
    upsert_sql = conn.execute.await_args_list[0].args[0]
    assert "INSERT INTO agents" in upsert_sql
    # the first event of a new agent must count itself (event_count = 1, not 0)
    assert "VALUES ($1, $2, 1)" in upsert_sql
    assert "INSERT INTO events" in conn.fetchrow.await_args_list[0].args[0]
    # nothing touches the agents table outside the transaction
    for call in mock_pool.execute.await_args_list:
        assert "INSERT INTO agents" not in call.args[0]


@pytest.mark.asyncio
async def test_failed_event_insert_propagates_so_the_transaction_rolls_back(mock_pool):
    """The agent upsert and the event INSERT sit in one transaction, so an
    INSERT failure must propagate out of ``conn.transaction()`` (which then
    rolls the counter bump back) instead of being swallowed."""
    mock_pool.conn.fetchrow.side_effect = RuntimeError("insert failed")

    with pytest.raises(RuntimeError, match="insert failed"):
        await write_event(
            tenant_id="tenant1",
            agent="agent1",
            event="message",
            data={},
        )

    exit_args = mock_pool.conn.transaction.return_value.__aexit__.await_args.args
    assert exit_args[0] is RuntimeError


@pytest.mark.asyncio
async def test_unknown_parent_is_rejected_inside_the_transaction(mock_pool):
    mock_pool.conn.fetchval.return_value = None

    with pytest.raises(HTTPException) as exc:
        await write_event(
            tenant_id="tenant1",
            agent="agent1",
            event="message",
            data={},
            parent_id="12345678-1234-1234-1234-123456789abc",
        )

    assert exc.value.status_code == 400
    mock_pool.conn.execute.assert_not_awaited()

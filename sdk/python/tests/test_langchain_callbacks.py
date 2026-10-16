"""
Unit tests for ZizkaDBCallbackHandler.

Tests are fully mocked — no LangChain server or ZizkaDB instance required.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4


# ── Fixtures ───────────────────────────────────────────────────────────────

def _make_db(event_id: str = "evt-test") -> MagicMock:
    """Create a mock ZizkaDB that returns a LogResult-like object."""
    log_result = MagicMock()
    log_result.event_id = event_id

    db = MagicMock()
    db.log = AsyncMock(return_value=log_result)
    return db


def _make_handler(agent: str = "test-bot", session_id: str | None = None, db=None):
    from zizkadb.integrations.langchain.callbacks import ZizkaDBCallbackHandler
    return ZizkaDBCallbackHandler(db or _make_db(), agent=agent, session_id=session_id)


# ── on_chat_model_start ────────────────────────────────────────────────────

class TestOnChatModelStart:
    @pytest.mark.asyncio
    async def test_logs_llm_start_event(self):
        handler = _make_handler()
        msg = MagicMock()
        msg.type = "human"
        msg.content = "Hello"
        run_id = uuid4()

        await handler.on_chat_model_start(
            {"name": "gpt-4"}, [[msg]], run_id=run_id
        )

        handler.db.log.assert_called_once()
        call_kwargs = handler.db.log.call_args.kwargs
        assert call_kwargs["event"] == "llm_start"
        assert "messages" in call_kwargs["data"]

    @pytest.mark.asyncio
    async def test_stores_run_id_in_parents(self):
        handler = _make_handler()
        run_id = uuid4()
        await handler.on_chat_model_start({"name": "gpt-4"}, [[]], run_id=run_id)
        assert run_id in handler._run_parents

    @pytest.mark.asyncio
    async def test_sets_last_event_id(self):
        handler = _make_handler(db=_make_db("evt-llm-1"))
        await handler.on_chat_model_start({"name": "gpt-4"}, [[]], run_id=uuid4())
        assert handler.last_event_id == "evt-llm-1"


# ── on_llm_end ─────────────────────────────────────────────────────────────

class TestOnLlmEnd:
    @pytest.mark.asyncio
    async def test_logs_llm_end_event(self):
        handler = _make_handler()
        response = MagicMock()
        gen = MagicMock()
        gen.text = "The answer is 42."
        response.generations = [[gen]]

        await handler.on_llm_end(response, run_id=uuid4())

        call_kwargs = handler.db.log.call_args.kwargs
        assert call_kwargs["event"] == "llm_end"
        assert "The answer is 42." in call_kwargs["data"]["text"]


# ── on_llm_error (new) ────────────────────────────────────────────────────

class TestOnLlmError:
    @pytest.mark.asyncio
    async def test_logs_llm_error_event(self):
        handler = _make_handler()
        err = RuntimeError("context length exceeded")
        await handler.on_llm_error(err, run_id=uuid4())

        call_kwargs = handler.db.log.call_args.kwargs
        assert call_kwargs["event"] == "llm_error"
        assert "context length exceeded" in call_kwargs["data"]["error"]
        assert call_kwargs["data"]["type"] == "RuntimeError"


# ── on_chain_start (new) ──────────────────────────────────────────────────

class TestOnChainStart:
    @pytest.mark.asyncio
    async def test_logs_chain_start_event(self):
        handler = _make_handler()
        run_id = uuid4()
        await handler.on_chain_start(
            {"name": "MyChain"}, {"input": "hello"}, run_id=run_id
        )

        call_kwargs = handler.db.log.call_args.kwargs
        assert call_kwargs["event"] == "chain_start"
        assert call_kwargs["data"]["chain"] == "MyChain"

    @pytest.mark.asyncio
    async def test_stores_run_id_in_parents(self):
        handler = _make_handler()
        run_id = uuid4()
        await handler.on_chain_start({"name": "C"}, {}, run_id=run_id)
        assert run_id in handler._run_parents

    @pytest.mark.asyncio
    async def test_uses_id_list_when_name_missing(self):
        handler = _make_handler()
        await handler.on_chain_start(
            {"id": ["pkg", "module", "MyChain"]}, {}, run_id=uuid4()
        )
        call_kwargs = handler.db.log.call_args.kwargs
        assert call_kwargs["data"]["chain"] == "MyChain"


# ── on_chain_end (new) ────────────────────────────────────────────────────

class TestOnChainEnd:
    @pytest.mark.asyncio
    async def test_logs_chain_end_and_removes_run_id(self):
        handler = _make_handler()
        run_id = uuid4()
        handler._run_parents[run_id] = "parent-evt"

        await handler.on_chain_end({"output": "done"}, run_id=run_id)

        call_kwargs = handler.db.log.call_args.kwargs
        assert call_kwargs["event"] == "chain_end"
        assert run_id not in handler._run_parents


# ── on_chain_error ─────────────────────────────────────────────────────────

class TestOnChainError:
    @pytest.mark.asyncio
    async def test_logs_chain_error_and_removes_run_id(self):
        handler = _make_handler()
        run_id = uuid4()
        handler._run_parents[run_id] = "parent-evt"
        err = ValueError("chain failed")

        await handler.on_chain_error(err, run_id=run_id)

        call_kwargs = handler.db.log.call_args.kwargs
        assert call_kwargs["event"] == "chain_error"
        assert "chain failed" in call_kwargs["data"]["error"]
        assert run_id not in handler._run_parents


# ── on_tool_start ──────────────────────────────────────────────────────────

class TestOnToolStart:
    @pytest.mark.asyncio
    async def test_logs_tool_start(self):
        handler = _make_handler()
        run_id = uuid4()
        await handler.on_tool_start({"name": "search"}, "query text", run_id=run_id)

        call_kwargs = handler.db.log.call_args.kwargs
        assert call_kwargs["event"] == "tool_start"
        assert call_kwargs["data"]["tool"] == "search"
        assert call_kwargs["data"]["input"] == "query text"

    @pytest.mark.asyncio
    async def test_stores_run_id(self):
        handler = _make_handler()
        run_id = uuid4()
        await handler.on_tool_start({"name": "search"}, "q", run_id=run_id)
        assert run_id in handler._run_parents


# ── on_tool_end ───────────────────────────────────────────────────────────

class TestOnToolEnd:
    @pytest.mark.asyncio
    async def test_logs_tool_end_and_removes_run_id(self):
        handler = _make_handler()
        run_id = uuid4()
        handler._run_parents[run_id] = "tool-start-evt"

        await handler.on_tool_end("search result", run_id=run_id)

        call_kwargs = handler.db.log.call_args.kwargs
        assert call_kwargs["event"] == "tool_end"
        assert "search result" in call_kwargs["data"]["output"]
        assert run_id not in handler._run_parents


# ── on_tool_error (new) ───────────────────────────────────────────────────

class TestOnToolError:
    @pytest.mark.asyncio
    async def test_logs_tool_error(self):
        handler = _make_handler()
        run_id = uuid4()
        handler._run_parents[run_id] = "tool-start-evt"
        err = ConnectionError("search API down")

        await handler.on_tool_error(err, run_id=run_id)

        call_kwargs = handler.db.log.call_args.kwargs
        assert call_kwargs["event"] == "tool_error"
        assert "search API down" in call_kwargs["data"]["error"]
        assert call_kwargs["data"]["type"] == "ConnectionError"

    @pytest.mark.asyncio
    async def test_removes_run_id_on_error(self):
        handler = _make_handler()
        run_id = uuid4()
        handler._run_parents[run_id] = "tool-start-evt"
        await handler.on_tool_error(Exception("fail"), run_id=run_id)
        assert run_id not in handler._run_parents


# ── causal parent linkage ─────────────────────────────────────────────────

class TestCausalLinkage:
    @pytest.mark.asyncio
    async def test_llm_end_links_to_llm_start(self):
        db = _make_db("parent-evt-id")
        handler = _make_handler(db=db)
        run_id = uuid4()

        await handler.on_chat_model_start({"name": "gpt-4"}, [[]], run_id=run_id)
        db.log.reset_mock()
        db.log.return_value = MagicMock(event_id="end-evt-id")

        gen = MagicMock()
        gen.text = "done"
        response = MagicMock(generations=[[gen]])
        await handler.on_llm_end(response, run_id=run_id)

        call_kwargs = db.log.call_args.kwargs
        assert call_kwargs["parent_id"] == "parent-evt-id"

    @pytest.mark.asyncio
    async def test_tool_end_links_to_tool_start(self):
        db = _make_db("tool-start-evt")
        handler = _make_handler(db=db)
        run_id = uuid4()

        await handler.on_tool_start({"name": "search"}, "q", run_id=run_id)
        db.log.reset_mock()
        db.log.return_value = MagicMock(event_id="tool-end-evt")

        await handler.on_tool_end("result", run_id=run_id)

        call_kwargs = db.log.call_args.kwargs
        assert call_kwargs["parent_id"] == "tool-start-evt"

    @pytest.mark.asyncio
    async def test_child_uses_parent_event_id(self):
        db = _make_db("parent-evt-id")
        handler = _make_handler(db=db)

        parent_run_id = uuid4()
        child_run_id = uuid4()

        # Log parent
        await handler.on_chat_model_start(
            {"name": "gpt-4"}, [[]], run_id=parent_run_id
        )

        # Reset mock to capture child call
        db.log.reset_mock()
        db.log.return_value = MagicMock(event_id="child-evt-id")

        # Log child with parent_run_id
        await handler.on_tool_start(
            {"name": "search"}, "q",
            run_id=child_run_id,
            parent_run_id=parent_run_id,
        )

        call_kwargs = db.log.call_args.kwargs
        assert call_kwargs["parent_id"] == "parent-evt-id"

    @pytest.mark.asyncio
    async def test_no_parent_when_run_id_unknown(self):
        handler = _make_handler()
        unknown_parent = uuid4()

        await handler.on_tool_start(
            {"name": "search"}, "q",
            run_id=uuid4(),
            parent_run_id=unknown_parent,
        )

        call_kwargs = handler.db.log.call_args.kwargs
        assert call_kwargs["parent_id"] is None

    @pytest.mark.asyncio
    async def test_session_id_forwarded(self):
        handler = _make_handler(session_id="sess-xyz")
        await handler.on_chat_model_start({"name": "gpt-4"}, [[]], run_id=uuid4())
        call_kwargs = handler.db.log.call_args.kwargs
        assert call_kwargs["session_id"] == "sess-xyz"


# ── _run_parents eviction ─────────────────────────────────────────────────

class TestRunParentsEviction:
    @pytest.mark.asyncio
    async def test_evicts_oldest_when_limit_exceeded(self):
        from zizkadb.integrations.langchain.callbacks import (
            ZizkaDBCallbackHandler,
            _RUN_PARENT_MAX,
        )
        db = _make_db()
        handler = ZizkaDBCallbackHandler(db, agent="bot")

        # Pre-fill to the limit
        from uuid import uuid4
        oldest_id = uuid4()
        handler._run_parents[oldest_id] = "oldest-evt"
        for _ in range(_RUN_PARENT_MAX - 1):
            handler._run_parents[uuid4()] = "evt"

        assert len(handler._run_parents) == _RUN_PARENT_MAX

        # One more log should evict the oldest
        await handler.on_chat_model_start({"name": "gpt-4"}, [[]], run_id=uuid4())

        assert oldest_id not in handler._run_parents
        assert len(handler._run_parents) <= _RUN_PARENT_MAX


# ── Tool name on follow-up events ──────────────────────────────────────────

class TestToolNameOnFollowUps:
    @pytest.mark.asyncio
    async def test_tool_end_carries_the_tool_name(self):
        handler = _make_handler()
        run_id = uuid4()
        await handler.on_tool_start({"name": "lookup_order"}, "ORD-1", run_id=run_id)
        await handler.on_tool_end("shipped", run_id=run_id)

        end = handler.db.log.call_args.kwargs
        assert end["event"] == "tool_end"
        assert end["data"]["tool"] == "lookup_order"
        assert end["data"]["output"] == "shipped"
        assert run_id not in handler._run_tools

    @pytest.mark.asyncio
    async def test_tool_error_carries_the_tool_name(self):
        handler = _make_handler()
        run_id = uuid4()
        await handler.on_tool_start({"name": "refund"}, "ORD-1", run_id=run_id)
        await handler.on_tool_error(ValueError("no such order"), run_id=run_id)

        err = handler.db.log.call_args.kwargs
        assert err["event"] == "tool_error"
        assert err["data"]["tool"] == "refund"
        assert err["data"]["error"] == "no such order"

    @pytest.mark.asyncio
    async def test_tool_end_without_a_start_still_logs(self):
        handler = _make_handler()
        await handler.on_tool_end("out", run_id=uuid4())
        data = handler.db.log.call_args.kwargs["data"]
        assert data["output"] == "out"
        assert "tool" not in data

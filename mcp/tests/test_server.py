"""
ZizkaDB MCP server — unit tests.

Tests cover the _api() helper and all @mcp.tool() functions using
mocked httpx responses. No running server or database required.
"""

from __future__ import annotations

import json
import pytest
import importlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch


# ── Helpers ────────────────────────────────────────────────────────────────

def _mock_response(status_code: int, body: dict | list) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.is_success = 200 <= status_code < 300
    resp.text = json.dumps(body)
    resp.json = MagicMock(return_value=body)
    return resp


def _patch_api(body: dict | list, status: int = 200):
    """Patch _api() to return a fixed response dict."""
    return patch(
        "zizkadb_mcp.server._api",
        new=AsyncMock(return_value=body if status < 400 else {"error": str(body), "status": status}),
    )


# ── _api helper ────────────────────────────────────────────────────────────

class TestApiHelper:
    @pytest.mark.asyncio
    async def test_returns_error_dict_when_key_missing(self, monkeypatch):
        monkeypatch.setattr("zizkadb_mcp.server._KEY", "")
        from zizkadb_mcp.server import _api
        result = await _api("GET", "/events")
        assert "error" in result
        assert result["status"] == 401

    @pytest.mark.asyncio
    async def test_get_request(self, monkeypatch):
        monkeypatch.setattr("zizkadb_mcp.server._KEY", "zizkadb_dev_local")
        mock_resp = _mock_response(200, {"events": []})
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("zizkadb_mcp.server.httpx.AsyncClient", return_value=mock_client):
            from zizkadb_mcp.server import _api
            result = await _api("GET", "/events")
        assert result == {"events": []}

    @pytest.mark.asyncio
    async def test_post_request(self, monkeypatch):
        monkeypatch.setattr("zizkadb_mcp.server._KEY", "zizkadb_dev_local")
        body = {"event_id": "abc", "timestamp": "2026-01-01T00:00:00Z", "sequence_no": 1}
        mock_resp = _mock_response(201, body)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("zizkadb_mcp.server.httpx.AsyncClient", return_value=mock_client):
            from zizkadb_mcp.server import _api
            result = await _api("POST", "/events", {"agent": "bot", "event": "e", "data": {}})
        assert result["event_id"] == "abc"

    @pytest.mark.asyncio
    async def test_error_response_returns_error_dict(self, monkeypatch):
        monkeypatch.setattr("zizkadb_mcp.server._KEY", "zizkadb_dev_local")
        mock_resp = _mock_response(401, {"detail": "Invalid API key"})
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("zizkadb_mcp.server.httpx.AsyncClient", return_value=mock_client):
            from zizkadb_mcp.server import _api
            result = await _api("GET", "/events")
        assert "error" in result
        assert result["status"] == 401


# ── log_event ──────────────────────────────────────────────────────────────

class TestLogEvent:
    @pytest.mark.asyncio
    async def test_logs_basic_event(self):
        expected = {"event_id": "evt-1", "timestamp": "2026-01-01T00:00:00Z", "sequence_no": 1}
        with _patch_api(expected):
            from zizkadb_mcp.server import log_event
            result = await log_event(agent="bot", event="tool_call", data={"tool": "search"})
        assert result["event_id"] == "evt-1"

    @pytest.mark.asyncio
    async def test_auto_session_and_parent_when_empty(self):
        captured = {}

        async def capture(method, path, body=None):
            captured["body"] = body
            return {"event_id": "x", "timestamp": "t", "sequence_no": 1}

        with patch("zizkadb_mcp.server._api", new=capture):
            from zizkadb_mcp import session_state
            from zizkadb_mcp.server import log_event

            session_state.reset_chain()
            await log_event(agent="bot", event="e", data={})

        assert captured["body"]["session_id"]
        assert "parent_id" not in captured["body"]

        with patch("zizkadb_mcp.server._api", new=capture):
            from zizkadb_mcp.server import log_event

            await log_event(agent="bot", event="e2", data={})

        assert captured["body"]["parent_id"] == "x"

    @pytest.mark.asyncio
    async def test_includes_session_id_when_provided(self):
        captured = {}

        async def capture(method, path, body=None):
            captured["body"] = body
            return {"event_id": "x", "timestamp": "t", "sequence_no": 1}

        with patch("zizkadb_mcp.server._api", new=capture):
            from zizkadb_mcp.server import log_event
            await log_event(agent="bot", event="e", data={}, session_id="sess-1")

        assert captured["body"]["session_id"] == "sess-1"


# ── search_memory ──────────────────────────────────────────────────────────

class TestSearchMemory:
    @pytest.mark.asyncio
    async def test_search_returns_results(self):
        expected = {"results": [{"event_id": "e1", "score": 0.9}]}
        with _patch_api(expected):
            from zizkadb_mcp.server import search_memory
            result = await search_memory(query="billing issue")
        assert result == expected

    @pytest.mark.asyncio
    async def test_agent_filter_included_when_provided(self):
        captured = {}

        async def capture(method, path, body=None):
            captured["body"] = body
            return {}

        with patch("zizkadb_mcp.server._api", new=capture):
            from zizkadb_mcp.server import search_memory
            await search_memory(query="q", agent="support-bot")

        assert captured["body"]["agent"] == "support-bot"

    @pytest.mark.asyncio
    async def test_agent_filter_omitted_when_empty(self):
        captured = {}

        async def capture(method, path, body=None):
            captured["body"] = body
            return {}

        with patch("zizkadb_mcp.server._api", new=capture):
            from zizkadb_mcp.server import search_memory
            await search_memory(query="q")

        assert "agent" not in captured["body"]


# ── get_context ────────────────────────────────────────────────────────────

class TestGetContext:
    @pytest.mark.asyncio
    async def test_returns_context_string(self):
        api_resp = {"context": "=== Agent Memory ===\n...\n=== End Memory ===\n", "event_count": 3}
        with _patch_api(api_resp):
            from zizkadb_mcp.server import get_context
            result = await get_context(agent="bot", task="answer billing question")
        assert "Agent Memory" in result

    @pytest.mark.asyncio
    async def test_returns_empty_string_when_no_context(self):
        with _patch_api({"context": "", "event_count": 0}):
            from zizkadb_mcp.server import get_context
            result = await get_context(agent="bot", task="something")
        assert result == ""

    @pytest.mark.asyncio
    async def test_error_response_returned_as_string(self):
        with _patch_api({"error": "Embedding failed", "status": 400}, status=400):
            from zizkadb_mcp.server import get_context
            result = await get_context(agent="bot", task="task")
        # Should not raise, just return a string
        assert isinstance(result, str)


# ── why ────────────────────────────────────────────────────────────────────

class TestWhy:
    @pytest.mark.asyncio
    async def test_returns_causal_chain(self):
        expected = {"event_id": "evt-5", "chain_length": 3, "chain": []}
        with _patch_api(expected):
            from zizkadb_mcp.server import why
            result = await why(event_id="evt-5")
        assert result["chain_length"] == 3

    @pytest.mark.asyncio
    async def test_url_encodes_event_id(self):
        captured = {}

        async def capture(method, path, body=None):
            captured["path"] = path
            return {}

        with patch("zizkadb_mcp.server._api", new=capture):
            from zizkadb_mcp.server import why
            await why(event_id="evt with spaces")

        assert "evt+with+spaces" in captured["path"] or "evt%20with%20spaces" in captured["path"]


# ── forget ─────────────────────────────────────────────────────────────────

class TestForget:
    @pytest.mark.asyncio
    async def test_sends_delete_with_filter(self):
        captured = {}

        async def capture(method, path, body=None):
            captured["method"] = method
            captured["body"] = body
            return {"deleted_events": 3}

        with patch("zizkadb_mcp.server._api", new=capture):
            from zizkadb_mcp.server import forget
            result = await forget(filter_key="user_id", filter_value="user_123")

        assert captured["method"] == "DELETE"
        assert captured["body"]["filter_key"] == "user_id"
        assert captured["body"]["filter_value"] == "user_123"
        assert result["deleted_events"] == 3


# ── memory_diff ────────────────────────────────────────────────────────────

class TestMemoryDiff:
    @pytest.mark.asyncio
    async def test_returns_session_summary(self):
        expected = {"session_id": "sess-1", "event_count": 10, "has_errors": False}
        with _patch_api(expected):
            from zizkadb_mcp.server import memory_diff
            result = await memory_diff(session_id="sess-1")
        assert result["event_count"] == 10

    @pytest.mark.asyncio
    async def test_url_encodes_session_id(self):
        captured = {}

        async def capture(method, path, body=None):
            captured["path"] = path
            return {}

        with patch("zizkadb_mcp.server._api", new=capture):
            from zizkadb_mcp.server import memory_diff
            await memory_diff(session_id="sess/with/slashes")

        assert "sess/with/slashes" not in captured["path"]


class TestVerifyChainAndSearchMode:
    @pytest.mark.asyncio
    async def test_verify_chain_calls_the_chain_route(self):
        captured = {}

        async def capture(method, path, body=None):
            captured["call"] = (method, path)
            return {"intact": True}

        with patch("zizkadb_mcp.server._api", new=capture):
            from zizkadb_mcp.server import verify_chain
            result = await verify_chain(agent="support bot")

        assert result == {"intact": True}
        assert captured["call"] == ("GET", "/events/chain?agent=support+bot")

    @pytest.mark.asyncio
    async def test_search_mode_sent_only_when_given(self):
        bodies = []

        async def capture(method, path, body=None):
            bodies.append(body)
            return {}

        with patch("zizkadb_mcp.server._api", new=capture):
            from zizkadb_mcp.server import search_memory
            await search_memory(query="q", mode="keyword")
            await search_memory(query="q")

        assert bodies[0]["mode"] == "keyword" and "mode" not in bodies[1]

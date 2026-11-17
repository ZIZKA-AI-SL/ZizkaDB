"""MCP handoff tools: same token as the SDKs; Cloud and self-hosted paths."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from zizkadb_mcp import session_state
from zizkadb_mcp.handoff import decode_token, encode_token

# Made by the Python SDK: HandoffToken(e1, s1, planner, billing, m1, r1).encode()
SDK_TOKEN = "zdbh1.eyJ2IjoxLCJlIjoiZTEiLCJzIjoiczEiLCJmIjoicGxhbm5lciIsInQiOiJiaWxsaW5nIiwibSI6Im0xIiwiciI6InIxIn0"


@pytest.fixture(autouse=True)
def fresh_chain():
    session_state.reset_chain()
    yield
    session_state.reset_chain()


def test_token_matches_the_sdks():
    assert encode_token("e1", "s1", "planner", "billing", "m1", "r1") == SDK_TOKEN
    assert decode_token(SDK_TOKEN)["t"] == "billing"
    with pytest.raises(ValueError):
        decode_token("nope")


@pytest.mark.asyncio
async def test_send_handoff_on_cloud():
    api = AsyncMock(return_value={"event_id": "sent-1", "message_id": "m1", "received_event_id": "recv-1", "session_id": "s1"})
    with patch("zizkadb_mcp.server._api", new=api):
        from zizkadb_mcp.server import send_handoff
        session_state.record_event("cause-1")
        out = await send_handoff(agent="planner", to_agent="billing", message="refund 4411")

    method, path, body = api.call_args.args
    assert (method, path) == ("POST", "/a2a/messages")
    assert body["parent_id"] == "cause-1"
    assert out["recorded_by"] == "a2a"
    assert decode_token(out["handoff_token"])["r"] == "recv-1"
    assert session_state.get_last_event_id() == "sent-1"


@pytest.mark.asyncio
async def test_send_handoff_falls_back_to_an_event_when_self_hosted():
    api = AsyncMock(side_effect=[{"error": "Not Found", "status": 404}, {"event_id": "evt-1"}])
    with patch("zizkadb_mcp.server._api", new=api):
        from zizkadb_mcp.server import send_handoff
        out = await send_handoff(agent="planner", to_agent="billing", message="hi")

    _, path, body = api.call_args.args
    assert path == "/events"
    assert body["event"] == "a2a.message"
    assert out["recorded_by"] == "event"


@pytest.mark.asyncio
async def test_continue_handoff_records_receipt_and_links_next_events():
    token = encode_token("sent-1", "s1", "planner", "billing")
    api = AsyncMock(return_value={"event_id": "recv-9"})
    with patch("zizkadb_mcp.server._api", new=api):
        from zizkadb_mcp.server import continue_handoff
        out = await continue_handoff(handoff_token=token, agent="billing")

    _, path, body = api.call_args.args
    assert path == "/events"
    assert body["event"] == "a2a.received"
    assert body["parent_id"] == "sent-1"
    assert out["received_event_id"] == "recv-9"
    assert session_state.get_last_event_id() == "recv-9"
    assert session_state.get_session_id() == "s1"


@pytest.mark.asyncio
async def test_continue_handoff_reuses_a_server_receipt_and_rejects_bad_tokens():
    api = AsyncMock()
    with patch("zizkadb_mcp.server._api", new=api):
        from zizkadb_mcp.server import continue_handoff
        out = await continue_handoff(handoff_token=SDK_TOKEN, agent="billing")
        bad = await continue_handoff(handoff_token="nope", agent="billing")

    api.assert_not_called()
    assert out["received_event_id"] == "r1"
    assert bad["status"] == 400

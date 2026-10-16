"""MCP proxy: tool results and errors are logged against the tool that was called."""

from __future__ import annotations

import io
import json
import sys

import pytest

from zizkadb_mcp import proxy

# A stand-in MCP server: answers id 1 with a result, id 2 with an error, and
# sends one notification that belongs to no call.
_CHILD = r"""
import json, sys
print(json.dumps({"jsonrpc": "2.0", "method": "notifications/progress"}), flush=True)
for line in sys.stdin:
    msg = json.loads(line)
    if msg.get("id") == 2:
        out = {"jsonrpc": "2.0", "id": 2, "error": {"code": -32000, "message": "boom"}}
    else:
        out = {"jsonrpc": "2.0", "id": msg.get("id"), "result": {"ok": True}}
    print(json.dumps(out), flush=True)
"""


class _Stdout:
    def __init__(self) -> None:
        self.buffer = io.BytesIO()


@pytest.mark.asyncio
async def test_results_and_errors_carry_the_tool_name(monkeypatch):
    logged: list[tuple[str, dict]] = []

    async def fake_log_tool(agent: str, event: str, data: dict) -> None:
        logged.append((event, data))

    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "read_file", "arguments": {"path": "a.txt"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "write_file"}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
    ]
    monkeypatch.setattr(proxy, "log_tool", fake_log_tool)
    monkeypatch.setattr(sys, "stdin", io.StringIO("".join(json.dumps(r) + "\n" for r in requests)))
    monkeypatch.setattr(sys, "stdout", _Stdout())

    code = await proxy.run_proxy("cursor-agent", [sys.executable, "-c", _CHILD])

    assert code == 0
    by_event = {event: data for event, data in logged}
    assert [e for e, _ in logged].count("mcp_tool_call") == 2
    assert by_event["mcp_tool_result"] == {"tool": "read_file", "result": {"ok": True}}
    assert by_event["mcp_tool_error"] == {
        "tool": "write_file", "error": {"code": -32000, "message": "boom"},
    }
    # tools/list (id 3) and the notification are forwarded but never logged.
    assert len(logged) == 4

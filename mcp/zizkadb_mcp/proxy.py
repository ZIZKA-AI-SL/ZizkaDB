#!/usr/bin/env python3
"""
MCP proxy — wrap a downstream MCP server and auto-log tool calls to ZizkaDB.

Usage:
  python -m zizkadb_mcp.proxy --agent cursor-agent -- server-command args...
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from zizkadb_mcp.server import _api
from zizkadb_mcp.session_state import get_last_event_id, get_session_id, record_event


async def log_tool(agent: str, event: str, data: dict) -> None:
    body = {
        "agent": agent,
        "event": event,
        "data": data,
        "session_id": get_session_id(),
    }
    parent = get_last_event_id()
    if parent:
        body["parent_id"] = parent
    result = await _api("POST", "/events", body)
    if isinstance(result, dict) and result.get("event_id"):
        record_event(result["event_id"])


async def run_proxy(agent: str, cmd: list[str]) -> int:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # JSON-RPC id → tool name for calls still in flight, so each result or
    # error is logged against the tool that produced it.
    pending: dict[str, str | None] = {}

    async def forward_stdin_to_child():
        assert proc.stdin and proc.stdout
        while True:
            line = await asyncio.get_event_loop().run_in_executor(None, sys.stdin.readline)
            if not line:
                proc.stdin.close()
                break
            try:
                msg = json.loads(line)
                if msg.get("method") == "tools/call":
                    params = msg.get("params") or {}
                    if msg.get("id") is not None:
                        pending[json.dumps(msg["id"])] = params.get("name")
                    await log_tool(
                        agent,
                        "mcp_tool_call",
                        {"tool": params.get("name"), "arguments": params.get("arguments")},
                    )
            except json.JSONDecodeError:
                pass
            proc.stdin.write(line.encode())
            await proc.stdin.drain()

    async def forward_child_to_stdout():
        assert proc.stdout
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
                key = json.dumps(msg.get("id")) if isinstance(msg, dict) else None
                if key in pending:
                    tool = pending.pop(key)
                    if msg.get("error") is not None:
                        await log_tool(agent, "mcp_tool_error", {"tool": tool, "error": msg["error"]})
                    else:
                        await log_tool(agent, "mcp_tool_result", {"tool": tool, "result": msg.get("result")})
            except json.JSONDecodeError:
                pass
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()

    await asyncio.gather(forward_stdin_to_child(), forward_child_to_stdout())
    return await proc.wait()


def main() -> None:
    parser = argparse.ArgumentParser(description="ZizkaDB MCP proxy")
    parser.add_argument("--agent", required=True, help="Agent name for logging")
    parser.add_argument("cmd", nargs=argparse.REMAINDER, help="Downstream MCP server command")
    args = parser.parse_args()
    cmd = args.cmd
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        raise SystemExit("Provide downstream MCP server command after --")
    raise SystemExit(asyncio.run(run_proxy(args.agent, cmd)))


if __name__ == "__main__":
    main()

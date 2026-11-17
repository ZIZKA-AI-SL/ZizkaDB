"""Causal chain completeness analysis for why() responses."""

from __future__ import annotations

MID_CHAIN_EVENT_TYPES = frozenset({
    "tool_call",
    "tool_start",
    "tool_end",
    "tool_result",
    "tool_error",
    "api_call",
    "llm_end",
    "llm_error",
    "chain_end",
    "chain_error",
    "retrieval",
    "rerank",
    "chunk_selected",
    "crew_task",
    "crew_output",
    "graph_node_done",
    "mcp_tool_call",
    "mcp_tool_result",
    "mcp_tool_error",
    "decision",
    "assistant_response",
})

ROOT_EVENT_TYPES = frozenset({
    "user_message",
    "crew_kickoff",
    "graph_node",
    "livekit_session_start",
    "session_start",
})


def analyze_why_chain(
    *,
    anchor_event_type: str,
    anchor_parent_id: str | None,
    chain_length: int,
    depth_limit: int,
    root_event_type: str | None,
    root_has_parent: bool,
    scoped_agent: str | None,
    chain_agents: set[str],
    continues_in: dict | None = None,
) -> dict:
    orphan = anchor_parent_id is None and anchor_event_type in MID_CHAIN_EVENT_TYPES
    depth_truncated = chain_length >= depth_limit and root_has_parent
    # A key scoped to one agent sees only that agent's part of the chain. The
    # walk stops at the first cause recorded by another agent (continues_in):
    # the chain is then incomplete, and the response says where it continues.
    scoped_agent_limited = bool(scoped_agent and (len(chain_agents) > 1 or continues_in))
    chain_complete = (
        not orphan
        and not depth_truncated
        and not scoped_agent_limited
        and (
            anchor_parent_id is not None
            or anchor_event_type in ROOT_EVENT_TYPES
            or (root_event_type in ROOT_EVENT_TYPES and not root_has_parent)
        )
    )
    return {
        "chain_complete": chain_complete,
        "orphan": orphan,
        "depth_truncated": depth_truncated,
        "scoped_agent_limited": scoped_agent_limited,
        "continues_in": continues_in,
    }


async def foreign_continuation(pool, tenant_id: str, rows: list, scoped_agent: str | None) -> dict | None:
    """Where a scoped walk stopped: the root's cause, when another agent recorded it.

    Returns ``{"agent", "event_id"}`` (no content) or None: unscoped walks,
    roots without a cause, and causes that no longer exist return None.
    """
    if not scoped_agent or not rows:
        return None
    root = max(rows, key=lambda r: r["depth"])
    parent = root["parent_event_id"]
    if parent is None or any(r["event_id"] == parent for r in rows):
        return None
    agent = await pool.fetchval(
        "SELECT agent_id FROM events WHERE event_id = $1 AND tenant_id = $2",
        parent,
        tenant_id,
    )
    if agent is None or agent == scoped_agent:
        return None
    return {"agent": agent, "event_id": str(parent)}

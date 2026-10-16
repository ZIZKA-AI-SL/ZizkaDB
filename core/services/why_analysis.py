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
) -> dict:
    orphan = anchor_parent_id is None and anchor_event_type in MID_CHAIN_EVENT_TYPES
    depth_truncated = chain_length >= depth_limit and root_has_parent
    scoped_agent_limited = bool(scoped_agent and len(chain_agents) > 1)
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
    }

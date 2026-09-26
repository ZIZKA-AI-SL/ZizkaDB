"""Tenant-wide session timeline and cross-agent why()."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from api.deps import get_tenant, assert_agent_allowed, is_unbound_api_key
from api.events import _format_event
from db.connection import get_pool
from services.exceptions import bad_request, not_found
from services.why_analysis import analyze_why_chain

router = APIRouter()

_UNBOUND_AGENT_REQUIRED = (
    "agent is required when using an unassigned API key. "
    "Pass ?agent= or log an event first to bind the key."
)


async def _resolve_agent_filter(tenant: dict, agent: str | None) -> str | None:
    """Agent to filter a session read by, without binding keys to arbitrary rows.

    Explicit ``agent`` is authorized (binding an unassigned key); otherwise a
    scoped key is limited to its own agent, an unbound key is rejected, and
    dashboard/dev sessions see every agent.
    """
    if agent:
        await assert_agent_allowed(tenant, agent)
        return agent
    if is_unbound_api_key(tenant):
        raise bad_request(_UNBOUND_AGENT_REQUIRED)
    return tenant.get("agent_id")


@router.get("")
async def list_sessions(
    limit: int = Query(default=20, ge=1, le=100),
    agent: str | None = None,
    tenant: dict = Depends(get_tenant),
):
    agent = await _resolve_agent_filter(tenant, agent)
    pool = get_pool()
    tenant_id = tenant["tenant_id"]

    conditions = ["tenant_id = $1", "session_id IS NOT NULL"]
    params: list[Any] = [tenant_id]
    i = 2
    if agent:
        conditions.append(f"agent_id = ${i}")
        params.append(agent)
        i += 1
    params.append(limit)
    where = " AND ".join(conditions)

    rows = await pool.fetch(
        f"""
        SELECT
            session_id,
            COUNT(*) AS event_count,
            COUNT(DISTINCT agent_id) AS agent_count,
            COUNT(DISTINCT event_type) AS event_types,
            MIN(timestamp) AS started_at,
            MAX(timestamp) AS ended_at,
            array_agg(DISTINCT agent_id ORDER BY agent_id) AS agents
        FROM events
        WHERE {where}
        GROUP BY session_id
        ORDER BY MAX(timestamp) DESC
        LIMIT ${i}
        """,
        *params,
    )
    return [
        {
            "session_id": r["session_id"],
            "event_count": r["event_count"],
            "agent_count": r["agent_count"],
            "event_types": r["event_types"],
            "started_at": r["started_at"].isoformat(),
            "ended_at": r["ended_at"].isoformat(),
            "agents": list(r["agents"]),
        }
        for r in rows
    ]


@router.get("/{session_id}/events")
async def session_events(
    session_id: str,
    limit: int = Query(default=500, ge=1, le=2000),
    agent: str | None = None,
    tenant: dict = Depends(get_tenant),
):
    scoped_agent = await _resolve_agent_filter(tenant, agent)
    pool = get_pool()
    tenant_id = tenant["tenant_id"]

    conditions = ["tenant_id = $1", "session_id = $2"]
    params: list[Any] = [tenant_id, session_id]
    if scoped_agent:
        conditions.append("agent_id = $3")
        params.append(scoped_agent)

    params.append(limit)
    where = " AND ".join(conditions)

    rows = await pool.fetch(
        f"""
        SELECT event_id, agent_id, timestamp, event_type,
               data, parent_event_id, session_id, sequence_no, metadata
        FROM events
        WHERE {where}
        ORDER BY timestamp ASC
        LIMIT ${len(params)}
        """,
        *params,
    )
    if not rows:
        return {"session_id": session_id, "events": []}

    return {
        "session_id": session_id,
        "events": [_format_event(r) for r in rows],
    }


@router.get("/{session_id}/why/{event_id}")
async def session_why(
    session_id: str,
    event_id: str,
    depth: int = Query(default=10, le=50),
    tenant: dict = Depends(get_tenant),
):
    pool = get_pool()
    tenant_id = tenant["tenant_id"]

    try:
        UUID(event_id)
    except ValueError:
        raise not_found("Event not found")

    anchor = await pool.fetchrow(
        """
        SELECT event_id, agent_id, event_type, parent_event_id, session_id
        FROM events
        WHERE event_id = $1 AND tenant_id = $2 AND session_id = $3
        """,
        event_id,
        tenant_id,
        session_id,
    )
    if anchor is None:
        raise not_found("Event not found in session")

    await assert_agent_allowed(tenant, anchor["agent_id"])
    # After the check the key is bound (or it is a dashboard/dev session):
    # never walk into another agent's events.
    scoped_agent = tenant.get("agent_id")

    rows = await pool.fetch(
        """
        WITH RECURSIVE causal_chain AS (
            SELECT
                event_id, agent_id, timestamp, event_type,
                data, parent_event_id, session_id, sequence_no, metadata,
                0 AS depth
            FROM events
            WHERE event_id = $1 AND tenant_id = $2

            UNION ALL

            SELECT
                e.event_id, e.agent_id, e.timestamp, e.event_type,
                e.data, e.parent_event_id, e.session_id, e.sequence_no, e.metadata,
                cc.depth + 1
            FROM events e
            INNER JOIN causal_chain cc ON e.event_id = cc.parent_event_id
            WHERE e.tenant_id = $2 AND cc.depth < $3
              AND ($4::text IS NULL OR e.agent_id = $4)
        )
        SELECT * FROM causal_chain
        ORDER BY depth DESC, timestamp ASC
        """,
        event_id,
        tenant_id,
        depth,
        scoped_agent,
    )

    ordered = sorted(rows, key=lambda r: r["depth"], reverse=True)
    root = ordered[0]
    chain_agents = {r["agent_id"] for r in rows}
    completeness = analyze_why_chain(
        anchor_event_type=anchor["event_type"],
        anchor_parent_id=str(anchor["parent_event_id"]) if anchor["parent_event_id"] else None,
        chain_length=len(rows),
        depth_limit=depth,
        root_event_type=root["event_type"],
        root_has_parent=root["parent_event_id"] is not None,
        scoped_agent=scoped_agent,
        chain_agents=chain_agents,
    )

    return {
        "session_id": session_id,
        "event_id": event_id,
        "chain_length": len(rows),
        "chain": [_format_event(r) for r in rows],
        **completeness,
    }

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from services.exceptions import not_found, bad_request
from typing import Any
from uuid import UUID
from datetime import datetime
import json
import os

from api.deps import get_tenant, assert_agent_allowed
from db.connection import get_pool
from services.checksum import verify_stored_checksum
from services.event_write import write_event
from services.why_analysis import analyze_why_chain

router = APIRouter()

_EVENTS_AT_MAX_ROWS = int(os.getenv("EVENTS_AT_MAX_ROWS", "10000"))


def _events_at_max_rows() -> int:
    return max(1, min(_EVENTS_AT_MAX_ROWS, 100_000))


# ─────────────────────────────────────────
# MODELS
# ─────────────────────────────────────────


class LogEventRequest(BaseModel):
    agent: str = Field(..., min_length=1, max_length=255)
    event: str = Field(..., min_length=1, max_length=255)
    data: dict[str, Any]
    parent_id: str | None = None
    session_id: str | None = None
    metadata: dict[str, Any] | None = None


# ─────────────────────────────────────────
# LOG EVENT — POST /v1/events
# ─────────────────────────────────────────


@router.post("", status_code=201)
async def log_event(
    body: LogEventRequest,
    tenant: dict = Depends(get_tenant),
):
    await assert_agent_allowed(tenant, body.agent)
    return await write_event(
        tenant_id=tenant["tenant_id"],
        agent=body.agent,
        event=body.event,
        data=body.data,
        parent_id=body.parent_id,
        session_id=body.session_id,
        metadata=body.metadata,
    )


# ─────────────────────────────────────────
# QUERY EVENTS — GET /v1/events
# ─────────────────────────────────────────


@router.get("")
async def query_events(
    agent: str,
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    before: datetime | None = None,
    after: datetime | None = None,
    event_type: str | None = None,
    session_id: str | None = None,
    tenant: dict = Depends(get_tenant),
):
    await assert_agent_allowed(tenant, agent)
    pool = get_pool()
    tenant_id = tenant["tenant_id"]

    conditions = ["tenant_id = $1", "agent_id = $2"]
    params: list[Any] = [tenant_id, agent]
    i = 3

    if before:
        conditions.append(f"timestamp < ${i}")
        params.append(before)
        i += 1
    if after:
        conditions.append(f"timestamp > ${i}")
        params.append(after)
        i += 1
    if event_type:
        conditions.append(f"event_type = ${i}")
        params.append(event_type)
        i += 1
    if session_id:
        conditions.append(f"session_id = ${i}")
        params.append(session_id)
        i += 1

    params.extend([limit, offset])
    where = " AND ".join(conditions)

    rows = await pool.fetch(
        f"""
        SELECT event_id, agent_id, timestamp, event_type,
               data, parent_event_id, session_id, sequence_no, metadata, index_status
        FROM events
        WHERE {where}
        ORDER BY timestamp DESC
        LIMIT ${i} OFFSET ${i + 1}
        """,
        *params,
    )

    return [_format_event(r) for r in rows]


# ─────────────────────────────────────────
# WHY — GET /v1/events/{event_id}/why
# Causal chain: walk parent_event_id tree
# ─────────────────────────────────────────


def _truncate_chain_to_agent(rows: list, agent_id: str) -> list:
    """Keep the chain prefix (from depth 0) whose events belong to ``agent_id``.

    Mirrors the scoped CTE, which stops recursing at the first event from
    another agent.
    """
    max_depth = len(rows)
    for r in rows:
        if r["agent_id"] != agent_id:
            max_depth = min(max_depth, r["depth"])
    # Preserve the query's ordering for the response.
    return [r for r in rows if r["depth"] < max_depth]


@router.get("/{event_id}/why")
async def why(
    event_id: str,
    depth: int = Query(default=10, le=50),
    tenant: dict = Depends(get_tenant),
):
    pool = get_pool()
    tenant_id = tenant["tenant_id"]
    # A scoped agent key may only walk the causal chain of its own agent's
    # events; anchoring on a mismatched agent yields no rows → 404.
    scoped_agent = tenant.get("agent_id")

    try:
        UUID(event_id)
    except ValueError:
        raise not_found("Event not found")

    rows = await pool.fetch(
        """
        WITH RECURSIVE causal_chain AS (
            SELECT
                event_id, agent_id, timestamp, event_type,
                data, parent_event_id, session_id, sequence_no, metadata,
                0 AS depth
            FROM events
            WHERE event_id = $1 AND tenant_id = $2
              AND ($4::text IS NULL OR agent_id = $4)

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

    if not rows:
        raise not_found("Event not found")

    anchor = next((r for r in rows if str(r["event_id"]) == event_id), None)
    if anchor is None:
        raise not_found("Event not found")

    await assert_agent_allowed(tenant, anchor["agent_id"])
    if scoped_agent is None and tenant.get("agent_id"):
        # The key was unassigned and just got bound by this request; the CTE
        # ran unfiltered, so drop other agents' events. The chain is a single
        # path, so cut it at the first foreign ancestor.
        scoped_agent = tenant["agent_id"]
        rows = _truncate_chain_to_agent(rows, scoped_agent)

    ordered = sorted(rows, key=lambda r: r["depth"], reverse=True)
    root = ordered[0]
    root_parent = root["parent_event_id"]
    chain_agents = {r["agent_id"] for r in rows}
    completeness = analyze_why_chain(
        anchor_event_type=anchor["event_type"],
        anchor_parent_id=str(anchor["parent_event_id"]) if anchor["parent_event_id"] else None,
        chain_length=len(rows),
        depth_limit=depth,
        root_event_type=root["event_type"],
        root_has_parent=root_parent is not None,
        scoped_agent=scoped_agent,
        chain_agents=chain_agents,
    )

    return {
        "event_id": event_id,
        "chain_length": len(rows),
        "chain": [_format_event(r) for r in rows],
        **completeness,
    }


# ─────────────────────────────────────────
# TIME TRAVEL — GET /v1/events/at
# Reconstruct agent state at a given time
# ─────────────────────────────────────────


@router.get("/at")
async def time_travel(
    agent: str,
    timestamp: datetime,
    tenant: dict = Depends(get_tenant),
):
    await assert_agent_allowed(tenant, agent)
    pool = get_pool()
    tenant_id = tenant["tenant_id"]
    max_rows = _events_at_max_rows()

    rows = await pool.fetch(
        """
        SELECT event_id, agent_id, timestamp, event_type,
               data, parent_event_id, session_id, sequence_no, metadata
        FROM events
        WHERE tenant_id = $1
          AND agent_id = $2
          AND timestamp <= $3
        ORDER BY timestamp ASC
        LIMIT $4
        """,
        tenant_id,
        agent,
        timestamp,
        max_rows + 1,
    )

    truncated = len(rows) > max_rows
    if truncated:
        rows = rows[:max_rows]

    # Reduce events to state (event sourcing pattern)
    state: dict[str, Any] = {}
    for row in rows:
        data = json.loads(row["data"]) if isinstance(row["data"], str) else dict(row["data"])
        if row["event_type"] == "STATE_SET":
            state.update(data)
        elif row["event_type"] == "STATE_DELETE":
            state.pop(data.get("key", ""), None)
        else:
            state["_last_event"] = {
                "event_id": str(row["event_id"]),
                "type": row["event_type"],
                "timestamp": row["timestamp"].isoformat(),
                "data": data,
            }

    return {
        "agent": agent,
        "at": timestamp.isoformat(),
        "event_count": len(rows),
        "truncated": truncated,
        "max_rows": max_rows,
        "state": state,
    }


# ─────────────────────────────────────────
# VERIFY CHECKSUM — GET /v1/events/verify
# Recompute checksums for an agent's recent events (audit helper).
# ─────────────────────────────────────────


@router.get("/verify")
async def verify_events(
    agent: str,
    limit: int = Query(default=100, ge=1, le=1000),
    before: datetime | None = None,
    after: datetime | None = None,
    tenant: dict = Depends(get_tenant),
):
    """Recompute the checksum of each returned event and compare with the
    stored value. Statuses: ``valid`` (v2), ``valid_legacy`` (v1, payload
    only), ``mismatch`` (tampering or corruption), ``missing``."""
    await assert_agent_allowed(tenant, agent)
    pool = get_pool()
    tenant_id = tenant["tenant_id"]

    conditions = ["tenant_id = $1", "agent_id = $2"]
    params: list[Any] = [tenant_id, agent]
    i = 3

    if before:
        conditions.append(f"timestamp < ${i}")
        params.append(before)
        i += 1
    if after:
        conditions.append(f"timestamp > ${i}")
        params.append(after)
        i += 1

    params.append(limit)
    where = " AND ".join(conditions)

    rows = await pool.fetch(
        f"""
        SELECT event_id, tenant_id, agent_id, timestamp, event_type,
               data, parent_event_id, session_id, sequence_no, metadata, checksum
        FROM events
        WHERE {where}
        ORDER BY timestamp DESC
        LIMIT ${i}
        """,
        *params,
    )

    results = [{"event_id": str(r["event_id"]), **verify_stored_checksum(dict(r))} for r in rows]
    summary = {"checked": len(results), "valid": 0, "valid_legacy": 0, "mismatch": 0, "missing": 0}
    for res in results:
        summary[res["status"]] += 1

    return {
        "agent": agent,
        "summary": summary,
        "mismatches": [r for r in results if r["status"] == "mismatch"],
    }


# ─────────────────────────────────────────
# VERIFY EVENT — GET /v1/events/{event_id}/verify
# ─────────────────────────────────────────


@router.get("/{event_id}/verify")
async def verify_event(
    event_id: str,
    tenant: dict = Depends(get_tenant),
):
    try:
        UUID(event_id)
    except ValueError:
        raise not_found("Event not found")

    pool = get_pool()
    tenant_id = tenant["tenant_id"]
    scoped_agent = tenant.get("agent_id")

    row = await pool.fetchrow(
        """
        SELECT event_id, tenant_id, agent_id, timestamp, event_type,
               data, parent_event_id, session_id, sequence_no, metadata, checksum
        FROM events
        WHERE event_id = $1 AND tenant_id = $2
        """,
        event_id,
        tenant_id,
    )
    if not row or (scoped_agent and row["agent_id"] != scoped_agent):
        raise not_found("Event not found")

    result = verify_stored_checksum(dict(row))
    result["event_id"] = event_id
    return result


# ─────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────


def _format_event(row) -> dict:
    data = row["data"]
    if isinstance(data, str):
        data = json.loads(data)

    out = {
        "event_id": str(row["event_id"]),
        "agent": row["agent_id"],
        "timestamp": row["timestamp"].isoformat(),
        "event": row["event_type"],
        "data": dict(data),
        "parent_id": str(row["parent_event_id"]) if row["parent_event_id"] else None,
        "session_id": row["session_id"],
        "sequence_no": row["sequence_no"],
        **_format_metadata(row),
    }
    if "index_status" in row.keys():
        out["index_status"] = row["index_status"]
    return out


def _format_metadata(row) -> dict:
    if "metadata" not in row.keys():
        return {}
    meta = row["metadata"]
    if meta is None:
        return {"metadata": None}
    if isinstance(meta, str):
        meta = json.loads(meta)
    return {"metadata": dict(meta) if meta else None}

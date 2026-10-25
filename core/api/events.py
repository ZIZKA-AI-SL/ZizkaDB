from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from services.exceptions import not_found, bad_request
from typing import Any
from uuid import UUID
from datetime import datetime
import json

from api.deps import get_tenant, assert_agent_allowed, is_unbound_api_key
from db.connection import get_pool
from services.checksum import CHECKSUM_COLUMNS, fetch_legacy_cutoff, verify_stored_checksum
from services.audit_chain import verify_chain
from services.event_write import write_event
from services.why_analysis import analyze_why_chain

router = APIRouter()


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
    # Same key again (per agent) returns the first event instead of a duplicate.
    idempotency_key: str | None = Field(default=None, max_length=255)


# ─────────────────────────────────────────
# LOG EVENT — POST /v1/events
# ─────────────────────────────────────────

@router.post("", status_code=201)
async def log_event(
    body: LogEventRequest,
    tenant: dict = Depends(get_tenant),
    idempotency_key: str | None = Header(default=None, max_length=255),
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
        idempotency_key=body.idempotency_key or idempotency_key,
    )


# ─────────────────────────────────────────
# BATCH — POST /v1/events/batch
# ─────────────────────────────────────────

BATCH_MAX_EVENTS = 500


class BatchEventItem(LogEventRequest):
    # Parent written earlier in the same batch, by its position (0-based).
    parent_index: int | None = Field(default=None, ge=0)


class LogBatchRequest(BaseModel):
    events: list[BatchEventItem] = Field(..., min_length=1, max_length=BATCH_MAX_EVENTS)


@router.post("/batch")
async def log_events_batch(
    body: LogBatchRequest,
    tenant: dict = Depends(get_tenant),
):
    """Write up to 500 events in one request, in order.

    Each event is written like POST /v1/events (own checks, chain link and
    idempotency key), so one bad event does not reject the others: every
    result is ``{"index", "ok": true, ...event}`` or ``{"index", "ok": false,
    "status", "error"}``. ``parent_index`` links to an earlier event of the batch.
    """
    for agent in dict.fromkeys(e.agent for e in body.events):
        await assert_agent_allowed(tenant, agent)

    results: list[dict] = []
    written: dict[int, str] = {}
    for index, item in enumerate(body.events):
        parent_id = item.parent_id
        if item.parent_index is not None:
            if item.parent_index >= index or item.parent_index not in written:
                results.append({
                    "index": index, "ok": False, "status": 400,
                    "error": "parent_index must point to an earlier event of this batch that was written",
                })
                continue
            parent_id = written[item.parent_index]
        try:
            result = await write_event(
                tenant_id=tenant["tenant_id"],
                agent=item.agent,
                event=item.event,
                data=item.data,
                parent_id=parent_id,
                session_id=item.session_id,
                metadata=item.metadata,
                idempotency_key=item.idempotency_key,
            )
        except HTTPException as exc:
            results.append({"index": index, "ok": False, "status": exc.status_code, "error": exc.detail})
            continue
        written[index] = result["event_id"]
        results.append({"index": index, "ok": True, **result})

    return {
        "written": sum(r["ok"] for r in results),
        "failed": sum(not r["ok"] for r in results),
        "results": results,
    }


# ─────────────────────────────────────────
# EXPORT / IMPORT — portable NDJSON
# ─────────────────────────────────────────

EXPORT_PAGE = 1000
IMPORT_MAX_EVENTS = 1000


def _export_line(r) -> str:
    def _json(v):
        return json.loads(v) if isinstance(v, str) else v

    return json.dumps({
        "event_id": str(r["event_id"]),
        "agent": r["agent_id"],
        "event": r["event_type"],
        "data": _json(r["data"]),
        "metadata": _json(r["metadata"]),
        "parent_id": str(r["parent_event_id"]) if r["parent_event_id"] else None,
        "session_id": r["session_id"],
        "timestamp": r["timestamp"].isoformat(),
        "sequence_no": r["sequence_no"],
        "checksum": r["checksum"],
        "chain_hash": r["chain_hash"],
    }) + "\n"


@router.get("/export")
async def export_events(
    agent: str,
    after_seq: int = Query(default=0, ge=0),
    tenant: dict = Depends(get_tenant),
):
    """Every event of an agent as NDJSON (one JSON object per line), oldest
    first, streamed. Feed the lines to POST /v1/events/import to move an
    agent between installs (self-hosted ↔ Cloud) or keep an offline copy."""
    await assert_agent_allowed(tenant, agent)
    pool = get_pool()
    tenant_id = tenant["tenant_id"]

    async def lines():
        cursor = after_seq
        while True:
            rows = await pool.fetch(
                """
                SELECT event_id, agent_id, event_type, data, metadata, parent_event_id,
                       session_id, timestamp, sequence_no, checksum, chain_hash
                FROM events
                WHERE tenant_id = $1 AND agent_id = $2 AND sequence_no > $3
                ORDER BY sequence_no
                LIMIT $4
                """,
                tenant_id, agent, cursor, EXPORT_PAGE,
            )
            for r in rows:
                yield _export_line(r)
            if len(rows) < EXPORT_PAGE:
                return
            cursor = rows[-1]["sequence_no"]

    return StreamingResponse(
        lines(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{agent}.ndjson"'},
    )


class ImportEvent(BaseModel):
    event_id: str
    agent: str = Field(..., min_length=1, max_length=255)
    event: str = Field(..., min_length=1, max_length=255)
    data: dict[str, Any]
    metadata: dict[str, Any] | None = None
    parent_id: str | None = None
    session_id: str | None = None
    timestamp: datetime | None = None


class ImportRequest(BaseModel):
    events: list[ImportEvent] = Field(..., min_length=1, max_length=IMPORT_MAX_EVENTS)
    # Store everything under this agent instead of each line's own.
    agent: str | None = Field(default=None, min_length=1, max_length=255)


@router.post("/import")
async def import_events(
    body: ImportRequest,
    tenant: dict = Depends(get_tenant),
):
    """Import lines from GET /v1/events/export (send them oldest first, up to
    1000 per request). Original timestamps are kept; events get new ids,
    checksums and chain links here. Parent links are remapped to the new ids.
    Re-running an import is safe: each line is keyed on its original event_id,
    so lines already imported come back as duplicates."""
    tenant_id = tenant["tenant_id"]
    agents = dict.fromkeys(body.agent or e.agent for e in body.events)
    for agent in agents:
        await assert_agent_allowed(tenant, agent)

    pool = get_pool()
    new_ids: dict[str, str] = {}
    summary = {"imported": 0, "duplicates": 0, "failed": 0, "parents_dropped": 0}
    errors: list[dict] = []

    for index, item in enumerate(body.events):
        agent = body.agent or item.agent
        parent_id = None
        if item.parent_id:
            parent_id = new_ids.get(item.parent_id) or await pool.fetchval(
                """
                SELECT event_id::text FROM events
                WHERE tenant_id = $1 AND agent_id = $2 AND idempotency_key = $3
                """,
                tenant_id, agent, f"import:{item.parent_id}",
            )
            if parent_id is None:
                summary["parents_dropped"] += 1
        try:
            result = await write_event(
                tenant_id=tenant_id,
                agent=agent,
                event=item.event,
                data=item.data,
                parent_id=parent_id,
                session_id=item.session_id,
                metadata=item.metadata,
                idempotency_key=f"import:{item.event_id}",
                occurred_at=item.timestamp,
            )
        except HTTPException as exc:
            summary["failed"] += 1
            errors.append({"index": index, "event_id": item.event_id,
                           "status": exc.status_code, "error": exc.detail})
            continue
        new_ids[item.event_id] = result["event_id"]
        summary["duplicates" if result["duplicate"] else "imported"] += 1

    return {**summary, "errors": errors[:100]}


# ─────────────────────────────────────────
# QUERY EVENTS — GET /v1/events
# ─────────────────────────────────────────


def _parse_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        ts, seq = cursor.rsplit("|", 1)
        return datetime.fromisoformat(ts), int(seq)
    except (ValueError, TypeError):
        raise bad_request("cursor must be '<timestamp>|<sequence_no>' from X-Next-Cursor")

@router.get("")
async def query_events(
    agent: str,
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    before: datetime | None = None,
    after: datetime | None = None,
    event_type: str | None = None,
    session_id: str | None = None,
    cursor: str | None = None,
    response: Response = None,
    tenant: dict = Depends(get_tenant),
):
    """Newest first. For deep paging use ``cursor`` (from the ``X-Next-Cursor``
    response header) instead of ``offset``: it stays fast on any page."""
    await assert_agent_allowed(tenant, agent)
    pool = get_pool()
    tenant_id = tenant["tenant_id"]

    conditions = ["tenant_id = $1", "agent_id = $2"]
    params: list[Any] = [tenant_id, agent]
    i = 3

    if cursor:
        cursor_ts, cursor_seq = _parse_cursor(cursor)
        conditions.append(f"(timestamp, sequence_no) < (${i}, ${i + 1})")
        params.extend([cursor_ts, cursor_seq])
        i += 2

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
        ORDER BY timestamp DESC, sequence_no DESC
        LIMIT ${i} OFFSET ${i + 1}
        """,
        *params,
    )

    if response is not None and len(rows) == limit:
        last = rows[-1]
        response.headers["X-Next-Cursor"] = f"{last['timestamp'].isoformat()}|{last['sequence_no']}"
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
        event_id, tenant_id, depth, scoped_agent,
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
# IMPACT — GET /v1/events/{event_id}/impact
# Walk the causal graph downstream: what did this event cause?
# ─────────────────────────────────────────

# Depth bounds how far the walk goes, not how wide: one event can have any
# number of children. Cap the nodes returned and say when the cap was hit.
IMPACT_MAX_NODES = 500


@router.get("/{event_id}/impact")
async def impact(
    event_id: str,
    depth: int = Query(default=10, ge=0, le=50),
    tenant: dict = Depends(get_tenant),
):
    pool = get_pool()
    tenant_id = tenant["tenant_id"]

    try:
        UUID(event_id)
    except ValueError:
        raise not_found("Event not found")

    # Authorize on the anchor before walking, so the walk itself is always
    # scoped: a scoped key only anchors on its own agent's events (else 404),
    # and an unbound key is bound to the anchor's agent here.
    anchor_agent = await pool.fetchval(
        """
        SELECT agent_id FROM events
        WHERE event_id = $1 AND tenant_id = $2
          AND ($3::text IS NULL OR agent_id = $3)
        """,
        event_id, tenant_id, tenant.get("agent_id"),
    )
    if anchor_agent is None:
        raise not_found("Event not found")
    await assert_agent_allowed(tenant, anchor_agent)
    scoped_agent = tenant.get("agent_id")

    # No ORDER BY around the CTE: Postgres then evaluates the recursion only
    # as far as the LIMIT fetches, and it emits rows level by level, so the
    # cap keeps the shallowest nodes. Within the cut level the choice is
    # arbitrary; the response is sorted below.
    rows = await pool.fetch(
        """
        WITH RECURSIVE impact_tree AS (
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
                it.depth + 1
            FROM events e
            INNER JOIN impact_tree it ON e.parent_event_id = it.event_id
            WHERE e.tenant_id = $2 AND it.depth < $3
              AND ($4::text IS NULL OR e.agent_id = $4)
        )
        SELECT * FROM impact_tree
        LIMIT $5
        """,
        event_id, tenant_id, depth, scoped_agent, IMPACT_MAX_NODES + 1,
    )

    truncated = len(rows) > IMPACT_MAX_NODES
    rows = sorted(rows[:IMPACT_MAX_NODES], key=lambda r: (r["depth"], r["sequence_no"]))

    # Does the walk stop at the depth limit while events lie beyond it?
    depth_truncated = False
    if not truncated:
        frontier = [r["event_id"] for r in rows if r["depth"] == depth]
        if frontier:
            depth_truncated = await pool.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1 FROM events
                    WHERE tenant_id = $1 AND parent_event_id = ANY($2::uuid[])
                      AND ($3::text IS NULL OR agent_id = $3)
                )
                """,
                tenant_id, frontier, scoped_agent,
            )

    return {
        "event_id": event_id,
        "node_count": len(rows),
        "truncated": truncated,
        "depth_truncated": depth_truncated,
        "nodes": [{**_format_event(r), "depth": r["depth"]} for r in rows],
    }


# ─────────────────────────────────────────
# TIME TRAVEL — GET /v1/events/at
# Reconstruct agent state at a given time
# ─────────────────────────────────────────

# The state at T is, per key, the newest write to that key at or before T: a
# STATE_SET carrying the key, a STATE_DELETE naming it (the key is then absent),
# or, for "_last_event", the newest event of any other type. Postgres picks the
# winner per key in one pass over the agent's state events, so the answer is exact
# for any history length and one row per surviving key leaves the database.
# Ties on timestamp go to sequence_no.
_STATE_AT_SQL = """
    WITH state_writes AS (
        SELECT w.key, w.value_json, NULL::uuid AS event_id, s.timestamp, s.sequence_no
        FROM events s
        CROSS JOIN LATERAL (
            SELECT kv.key, kv.value::text AS value_json
            FROM jsonb_each(
                CASE WHEN s.event_type = 'STATE_SET' AND jsonb_typeof(s.data) = 'object'
                     THEN s.data ELSE '{}'::jsonb END
            ) AS kv

            UNION ALL

            -- The key dict.pop(data.get("key", "")) would remove: the named key
            -- when it is a string, "" when the field is missing, nothing otherwise.
            -- A NULL value marks the key as deleted.
            SELECT COALESCE(s.data->>'key', ''), NULL
            WHERE s.event_type = 'STATE_DELETE'
              AND jsonb_typeof(s.data) = 'object'
              AND (NOT (s.data ? 'key') OR jsonb_typeof(s.data->'key') = 'string')
        ) AS w
        WHERE s.tenant_id = $1
          AND s.agent_id = $2
          AND s.timestamp <= $3
          AND s.event_type IN ('STATE_SET', 'STATE_DELETE')
    ),
    last_event AS (
        SELECT '_last_event'::text AS key, NULL::text AS value_json, event_id,
               timestamp, sequence_no
        FROM events
        WHERE tenant_id = $1
          AND agent_id = $2
          AND timestamp <= $3
          AND event_type NOT IN ('STATE_SET', 'STATE_DELETE')
        ORDER BY timestamp DESC, sequence_no DESC
        LIMIT 1
    ),
    latest AS (
        SELECT DISTINCT ON (key) key, value_json, event_id
        FROM (SELECT * FROM state_writes UNION ALL SELECT * FROM last_event) AS w
        ORDER BY key, timestamp DESC, sequence_no DESC
    )
    SELECT l.key, l.value_json, e.event_id, e.event_type, e.timestamp,
           e.data::text AS event_json
    FROM latest l
    LEFT JOIN events e ON e.event_id = l.event_id
    WHERE l.value_json IS NOT NULL OR l.event_id IS NOT NULL
    ORDER BY l.key
"""


@router.get("/at")
async def time_travel(
    agent: str,
    timestamp: datetime,
    tenant: dict = Depends(get_tenant),
):
    await assert_agent_allowed(tenant, agent)
    pool = get_pool()
    tenant_id = tenant["tenant_id"]

    # One snapshot for both reads, so event_count and state describe the same history.
    async with pool.acquire() as conn:
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            event_count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM events
                WHERE tenant_id = $1 AND agent_id = $2 AND timestamp <= $3
                """,
                tenant_id, agent, timestamp,
            )
            rows = await conn.fetch(_STATE_AT_SQL, tenant_id, agent, timestamp)

    state: dict[str, Any] = {}
    for row in rows:
        if row["event_id"] is None:
            state[row["key"]] = json.loads(row["value_json"])
        else:
            state["_last_event"] = {
                "event_id": str(row["event_id"]),
                "type": row["event_type"],
                "timestamp": row["timestamp"].isoformat(),
                "data": json.loads(row["event_json"]),
            }

    return {
        "agent": agent,
        "at": timestamp.isoformat(),
        "event_count": event_count,
        # Kept for clients that read it; the state above is never cut short.
        "truncated": False,
        "state": state,
    }


# ─────────────────────────────────────────
# VERIFY CHECKSUMS — GET /v1/events/verify
# Recompute checksums for an agent's events (audit helper)
# ─────────────────────────────────────────

@router.get("/chain")
async def verify_event_chain(
    agent: str,
    after_seq: int | None = Query(default=None, ge=0),
    anchor: str | None = Query(default=None, min_length=64, max_length=64),
    tenant: dict = Depends(get_tenant),
):
    """Check the agent's hash chain: no event removed, inserted, reordered or changed.

    Walks every event (and tombstone of a legitimately deleted one) in order,
    recomputing checksums and links. Returns ``intact``, a summary, up to 100
    problems and ``chain_head``. Store ``chain_head`` outside ZizkaDB; later pass
    its ``sequence_no`` as ``after_seq`` and its ``chain_hash`` as ``anchor`` to
    check only newer events and prove nothing before the head was rewritten.
    """
    await assert_agent_allowed(tenant, agent)
    if (after_seq is None) != (anchor is None):
        raise bad_request("Pass after_seq and anchor together (from a stored chain_head)")
    pool = get_pool()
    return await verify_chain(
        pool,
        tenant["tenant_id"],
        agent,
        after_seq=after_seq,
        anchor=anchor,
        legacy_until_seq=await fetch_legacy_cutoff(pool),
    )


@router.get("/verify")
async def verify_events(
    agent: str,
    limit: int = Query(default=100, ge=1, le=1000),
    before: datetime | None = None,
    after: datetime | None = None,
    before_seq: int | None = Query(default=None, ge=1),
    tenant: dict = Depends(get_tenant),
):
    """Recompute each event's checksum and compare with the stored value.

    Statuses: ``valid`` (v2, all audit fields), ``valid_legacy`` (v1, payload
    only, rows written before v2), ``mismatch`` (tampering or corruption),
    ``missing``. Results are newest first (by ``sequence_no``); fetch the next
    page by passing ``next_before_seq`` as ``before_seq`` until it is null.
    ``before`` / ``after`` optionally bound the window by timestamp.
    """
    await assert_agent_allowed(tenant, agent)
    if before and after and after >= before:
        raise bad_request("'after' must be earlier than 'before'")

    pool = get_pool()
    conditions = ["tenant_id = $1", "agent_id = $2"]
    params: list[Any] = [tenant["tenant_id"], agent]

    if before:
        params.append(before)
        conditions.append(f"timestamp < ${len(params)}")
    if after:
        params.append(after)
        conditions.append(f"timestamp > ${len(params)}")
    # sequence_no is unique, so paging on it never skips rows that share a
    # timestamp with the last row of the previous page.
    if before_seq:
        params.append(before_seq)
        conditions.append(f"sequence_no < ${len(params)}")
    params.append(limit)

    rows = await pool.fetch(
        f"""
        SELECT {CHECKSUM_COLUMNS}
        FROM events
        WHERE {" AND ".join(conditions)}
        ORDER BY sequence_no DESC
        LIMIT ${len(params)}
        """,
        *params,
    )
    legacy_until_seq = await fetch_legacy_cutoff(pool)

    summary = {"checked": len(rows), "valid": 0, "valid_legacy": 0, "mismatch": 0, "missing": 0}
    mismatches = []
    for r in rows:
        result = verify_stored_checksum(r, legacy_until_seq)
        summary[result["status"]] += 1
        if result["status"] == "mismatch":
            mismatches.append({
                "event_id": str(r["event_id"]),
                "sequence_no": r["sequence_no"],
                "timestamp": r["timestamp"].isoformat(),
                **result,
            })

    return {
        "agent": agent,
        "summary": summary,
        "mismatches": mismatches,
        "next_before_seq": rows[-1]["sequence_no"] if len(rows) == limit else None,
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
        event_uuid = UUID(event_id)
    except ValueError:
        raise not_found("Event not found")
    # A key not yet bound to an agent must not read tenant-wide rows, and
    # asserting on the row's agent would bind the key to whichever agent owns
    # the event it guessed. It can verify per agent via /verify?agent=.
    if is_unbound_api_key(tenant):
        raise not_found("Event not found")

    pool = get_pool()
    # A scoped agent key cannot see other agents' events.
    scoped_agent = tenant.get("agent_id")
    row = await pool.fetchrow(
        f"""
        SELECT {CHECKSUM_COLUMNS}
        FROM events
        WHERE event_id = $1 AND tenant_id = $2
          AND ($3::text IS NULL OR agent_id = $3)
        """,
        event_uuid, tenant["tenant_id"], scoped_agent,
    )
    if row is None:
        raise not_found("Event not found")

    await assert_agent_allowed(tenant, row["agent_id"])

    result = verify_stored_checksum(row, await fetch_legacy_cutoff(pool))
    return {"event_id": str(row["event_id"]), "agent": row["agent_id"], **result}


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

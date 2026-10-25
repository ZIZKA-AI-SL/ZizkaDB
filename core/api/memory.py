"""
Memory API — three endpoints that make ZizkaDB easy to use as a drop-in
replacement for LLM-provided memory:

  POST /v1/memory/context  →  get a prompt-ready context block
  GET  /v1/memory/diff     →  what changed after a session
  DELETE /v1/memory/forget →  GDPR forget by any metadata field
"""

from fastapi import APIRouter, Depends
from services.exceptions import bad_request, not_found, service_unavailable
from pydantic import BaseModel, Field
from typing import Any
import asyncio
import hashlib
import json
import logging

from api.deps import get_tenant, assert_agent_allowed, is_unbound_api_key, get_writer_tenant
from db.connection import get_pool, get_qdrant
from services.audit_chain import tombstone_events
from api.search import vector_search
from services.embeddings import generate_embedding
from services.keyword_search import keyword_search
from services.memory_rank import superseded_ids, weighted_score
from services.entitlements import embeddings_enabled, is_self_hosted_deployment

router = APIRouter()
log = logging.getLogger(__name__)


# ─────────────────────────────────────────
# MODELS
# ─────────────────────────────────────────

class ContextRequest(BaseModel):
    agent: str
    task: str
    # Bounds mirror SearchRequest / the list routes: a negative recent_limit
    # reached Postgres as LIMIT $4 and errored (500), and unbounded limits
    # allowed an arbitrarily large events / vector fetch.
    max_tokens: int = Field(default=2000, ge=1)
    session_id: str | None = None   # exclude current session from context
    recent_limit: int = Field(default=10, ge=1, le=100)   # recent events to always include
    semantic_limit: int = Field(default=10, ge=1, le=100) # semantically relevant events


class ForgetRequest(BaseModel):
    filter_key: str    # e.g. "user_id", "session_id", "email"
    filter_value: str  # e.g. "user_123"


# ─────────────────────────────────────────
# CONTEXT INJECTION
# ─────────────────────────────────────────

@router.post("/context")
async def get_context(
    body: ContextRequest,
    tenant: dict = Depends(get_tenant),
):
    """
    Returns a formatted context block ready to inject into a system prompt.

    Example usage:
        context = await db.context_for(agent="my-bot", task="user asking about billing")
        messages = [
            {"role": "system", "content": f"You are a support bot.\\n\\n{context}"},
            {"role": "user", "content": user_message}
        ]
    """
    # A scoped agent key may only read its own agent's context.
    await assert_agent_allowed(tenant, body.agent)
    tenant_id = tenant["tenant_id"]
    pool = get_pool()

    # 1. Recent events — always include these for temporal grounding
    recent_rows = await pool.fetch(
        """
        SELECT event_id, agent_id, timestamp, event_type, data, session_id, metadata
        FROM events
        WHERE tenant_id = $1 AND agent_id = $2
          AND ($3::text IS NULL OR session_id::text != $3)
        ORDER BY timestamp DESC
        LIMIT $4
        """,
        tenant_id, body.agent, body.session_id, body.recent_limit,
    )

    # 2. Relevant events: vector search when embeddings are on, keyword search
    # (Postgres full-text) when they are off or no embedding could be made.
    semantic_rows = []
    relevant_source = "semantic"
    vector_hits = None
    if embeddings_enabled():
        try:
            vector_hits = await vector_search(
                tenant_id, body.task, agent=body.agent, limit=body.semantic_limit
            )
        except Exception as e:
            log.warning("Semantic search failed for context: %s", e)
    if vector_hits:
        score_map = {eid: round(score, 3) for eid, score in vector_hits}
        sem_rows = await pool.fetch(
            """
            SELECT event_id, agent_id, timestamp, event_type, data, session_id, metadata
            FROM events
            WHERE event_id = ANY($1::uuid[]) AND tenant_id = $2
              AND ($3::text IS NULL OR session_id::text != $3)
            """,
            list(score_map), tenant_id, body.session_id,
        )
        for row in sem_rows:
            semantic_rows.append((row, score_map.get(str(row["event_id"]), 0.0)))
    else:
        relevant_source = "keyword"
        semantic_rows = [
            (row, round(score, 3))
            for row, score in await keyword_search(
                pool, tenant_id, body.task, agent=body.agent,
                limit=body.semantic_limit, exclude_session=body.session_id,
            )
        ]

    # 3. Merge — deduplicate, recent first then semantic fills gaps
    # Recent events take priority: they provide temporal grounding. Semantic
    # results that overlap with recent are skipped to avoid duplication.
    # Superseded facts (an explicit `supersedes`, or a STATE_SET whose keys
    # were all written again) stay in the log but not in memory.
    hidden = await superseded_ids(
        pool, tenant_id, body.agent,
        [r["event_id"] for r in recent_rows] + [r["event_id"] for r, _ in semantic_rows],
    )
    # Relevant events rank by relevance × importance × recency.
    semantic_rows = [
        (row, round(weighted_score(score, row["timestamp"], row.get("metadata")), 4))
        for row, score in semantic_rows
    ]

    seen_ids = set(hidden)
    seen_content = set()
    merged = []

    def add(row, source, score):
        eid = str(row["event_id"])
        # Exact repeats (same type and data) would only spend the token budget.
        content = (row["event_type"], json.dumps(row["data"], sort_keys=True, default=str))
        if eid in seen_ids or content in seen_content:
            return
        seen_ids.add(eid)
        seen_content.add(content)
        merged.append({"row": row, "source": source, "score": score})

    for row in recent_rows:
        add(row, "recent", 0.0)
    for row, score in sorted(semantic_rows, key=lambda x: x[1], reverse=True):
        add(row, relevant_source, score)

    # 4. Format as a prompt-ready string within token budget
    # Rough estimate: 1 token ≈ 4 chars
    char_budget = body.max_tokens * 4
    lines = []
    total_chars = 0

    header = "=== Agent Memory ===\n"
    footer = "=== End Memory ===\n"
    total_chars += len(header) + len(footer)

    for item in merged:
        row = item["row"]
        data = row["data"]
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                pass

        ts = row["timestamp"].strftime("%Y-%m-%d %H:%M")
        source_label = "Recent" if item["source"] == "recent" else "Relevant"
        data_str = _format_data(data)
        line = f"[{source_label} · {ts}] {row['event_type']}: {data_str}\n"

        if total_chars + len(line) > char_budget:
            break

        lines.append(line)
        total_chars += len(line)

    context_str = header + "".join(lines) + footer if lines else ""

    # 5. Build source references
    sources = [
        {
            "event_id": str(item["row"]["event_id"]),
            "event": item["row"]["event_type"],
            "timestamp": item["row"]["timestamp"].isoformat(),
            "relevance": item["score"],
            "source": item["source"],
        }
        for item in merged[:len(lines)]
    ]

    return {
        "context": context_str,
        "event_count": len(lines),
        "estimated_tokens": total_chars // 4,
        "sources": sources,
    }


# ─────────────────────────────────────────
# SESSION DIFF
# ─────────────────────────────────────────

@router.get("/diff/{session_id}")
async def session_diff(
    session_id: str,
    tenant: dict = Depends(get_tenant),
):
    """
    Returns a summary of what happened in a session and what patterns emerged.
    Useful after a session ends to understand what the agent learned.

    Example:
        diff = await db.memory_diff(session_id="sess_abc")
        print(diff["summary"])
        print(diff["new_patterns"])
    """
    if is_unbound_api_key(tenant):
        raise bad_request(
            "diff requires an agent-scoped API key: log an event with this key "
            "first, or use the dashboard for tenant-wide session diffs."
        )

    tenant_id = tenant["tenant_id"]
    pool = get_pool()

    scoped_agent = tenant.get("agent_id")

    # Events in this session; a scoped key only ever sees its own agent's rows.
    rows = await pool.fetch(
        """
        SELECT event_id, agent_id, event_type, data, timestamp, parent_event_id
        FROM events
        WHERE tenant_id = $1 AND session_id = $2
          AND ($3::text IS NULL OR agent_id = $3)
        ORDER BY timestamp ASC
        """,
        tenant_id, session_id, scoped_agent,
    )

    if not rows:
        raise not_found("Session not found")

    agent_id = scoped_agent or rows[0]["agent_id"]
    await assert_agent_allowed(tenant, agent_id)
    events = []
    event_types: dict[str, int] = {}
    has_error = False

    for row in rows:
        data = row["data"]
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                pass

        et = row["event_type"]
        event_types[et] = event_types.get(et, 0) + 1
        if "error" in et.lower() or "fail" in et.lower():
            has_error = True

        events.append({
            "event_id": str(row["event_id"]),
            "event": et,
            "data": dict(data) if isinstance(data, dict) else data,
            "timestamp": row["timestamp"].isoformat(),
            "parent_id": str(row["parent_event_id"]) if row["parent_event_id"] else None,
        })

    # Causal chain depth
    root_events = [e for e in events if not e["parent_id"]]
    children_map: dict[str, list] = {}
    for e in events:
        if e["parent_id"]:
            children_map.setdefault(e["parent_id"], []).append(e["event_id"])
    max_depth = _compute_max_depth(root_events, children_map)

    # Patterns from prior sessions
    prior_common = await pool.fetchrow(
        """
        SELECT event_type, COUNT(*) as cnt
        FROM events
        WHERE tenant_id = $1 AND agent_id = $2 AND session_id::text != $3
        GROUP BY event_type ORDER BY cnt DESC LIMIT 1
        """,
        tenant_id, agent_id, session_id,
    )

    session_event_types = set(event_types.keys())
    new_patterns = []
    if prior_common:
        prior_types_rows = await pool.fetch(
            """
            SELECT DISTINCT event_type FROM events
            WHERE tenant_id = $1 AND agent_id = $2 AND session_id::text != $3
            """,
            tenant_id, agent_id, session_id,
        )
        prior_types = {r["event_type"] for r in prior_types_rows}
        new_patterns = list(session_event_types - prior_types)

    duration_seconds = None
    if len(rows) >= 2:
        duration_seconds = (rows[-1]["timestamp"] - rows[0]["timestamp"]).total_seconds()

    return {
        "session_id": session_id,
        "agent": agent_id,
        "event_count": len(events),
        "event_types": event_types,
        "causal_depth": max_depth,
        "has_errors": has_error,
        "duration_seconds": duration_seconds,
        "new_event_types": new_patterns,
        "top_events": events[:5],
        "summary": _summarise(agent_id, events, event_types, has_error, duration_seconds),
    }


# ─────────────────────────────────────────
# GDPR FORGET
# ─────────────────────────────────────────

@router.delete("/forget")
async def forget(
    body: ForgetRequest,
    tenant: dict = Depends(get_writer_tenant),
):
    """
    Delete all events where data contains a specific key-value pair.
    Also removes the vectors from Qdrant.

    Examples:
        await db.forget(filter_key="user_id", filter_value="user_123")
        await db.forget(filter_key="email", filter_value="user@company.com")
    """
    # An unbound key must not delete tenant-wide; only dashboard sessions may.
    if is_unbound_api_key(tenant):
        raise bad_request(
            "forget requires an agent-scoped API key: log an event with this key "
            "first, or use the dashboard for a tenant-wide forget."
        )
    tenant_id = tenant["tenant_id"]
    pool = get_pool()

    # A scoped agent key may only forget its own agent's events; a full key
    # forgets tenant-wide. Deletion below works off these SELECTed ids, so
    # constraining the SELECT is sufficient.
    scoped_agent = tenant.get("agent_id")

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Find matching event IDs
            rows = await conn.fetch(
                """
                SELECT event_id
                FROM events
                WHERE tenant_id = $1
                  AND data->$2 = to_jsonb($3::text)
                  AND ($4::text IS NULL OR agent_id = $4)
                """,
                tenant_id,
                body.filter_key,
                body.filter_value,
                scoped_agent,
            )

            if not rows:
                return {
                    "deleted_events": 0,
                    "filter": {
                        "key": body.filter_key,
                        "value": body.filter_value,
                    },
                    "message": "No matching events found",
                }

            event_ids = [r["event_id"] for r in rows]
            deleted = len(event_ids)

            # Serialize against the embedding worker (#234): take the
            # per-event advisory lock (the codebase's pattern, cf.
            # services/api_keys) before deleting vectors and rows, so a
            # worker that already selected one of these events cannot
            # recreate its vector once the deletion commits. Locks are
            # taken in sorted order so concurrent forgets cannot
            # deadlock, and released when the transaction ends.
            for eid in sorted(event_ids, key=str):
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))", str(eid)
                )

            # Vectors first: if the search index can't be cleaned, keep
            # the Postgres rows so a retry can still find (and delete)
            # the vectors. Deleting a missing Qdrant point is a no-op,
            # so retries are idempotent. Raising here rolls the
            # transaction back, so nothing is deleted.
            vector_cleanup = await _delete_vectors(event_ids, tenant_id)
            if not vector_cleanup and embeddings_enabled():
                raise service_unavailable(
                    "Search index unavailable; nothing was deleted. Retry forget shortly."
                )

            # Keep the deleted rows' chain links so the audit chain still verifies.
            await tombstone_events(conn, tenant_id, event_ids, "forget")

            # PostgreSQL does not support aggregate functions in RETURNING
            # clauses. We already know the count from the SELECT above, so
            # use that directly.
            await conn.execute(
                """
                DELETE FROM events
                WHERE tenant_id = $1
                  AND event_id = ANY($2::uuid[])
                """,
                tenant_id,
                event_ids,
            )

    # Never log the forgotten value itself (it is usually personal data).
    log.info(
        "GDPR forget: tenant=%s key=%s value_sha256=%s deleted=%s vector_cleanup=%s",
        tenant_id,
        body.filter_key,
        hashlib.sha256(body.filter_value.encode()).hexdigest()[:12],
        deleted,
        vector_cleanup,
    )

    message = f"Deleted {deleted} events."
    if vector_cleanup:
        message += " Vectors removed from search index."
    else:
        message += " Search index was unreachable; vector cleanup skipped."

    return {
        "deleted_events": deleted,
        "vector_cleanup": vector_cleanup,
        "filter": {
            "key": body.filter_key,
            "value": body.filter_value,
        },
        "message": message,
    }


_VECTOR_DELETE_ATTEMPTS = 3


async def _delete_vectors(event_ids: list, tenant_id: str) -> bool:
    """Delete event vectors from Qdrant with retries. Returns True on success."""
    from qdrant_client.models import PointIdsList

    points = PointIdsList(points=[str(eid) for eid in event_ids])
    for attempt in range(1, _VECTOR_DELETE_ATTEMPTS + 1):
        try:
            await get_qdrant().delete(
                collection_name="agent_events",
                points_selector=points,
            )
            return True
        except Exception as e:
            log.warning(
                "Qdrant delete attempt %d/%d failed for tenant %s: %s",
                attempt,
                _VECTOR_DELETE_ATTEMPTS,
                tenant_id,
                e,
            )
            if attempt < _VECTOR_DELETE_ATTEMPTS:
                await asyncio.sleep(0.5 * attempt)
    return False


# ─────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────

def _format_data(data: Any) -> str:
    if isinstance(data, dict):
        parts = []
        for k, v in list(data.items())[:4]:
            v_str = str(v)[:80]
            parts.append(f"{k}={v_str!r}")
        return ", ".join(parts)
    return str(data)[:120]


def _compute_max_depth(roots: list, children: dict, depth: int = 0) -> int:
    if not roots:
        return depth
    max_d = depth
    for r in roots:
        kids = children.get(r["event_id"], [])
        if kids:
            kid_nodes = [{"event_id": k} for k in kids]
            d = _compute_max_depth(kid_nodes, children, depth + 1)
            max_d = max(max_d, d)
    return max_d


def _summarise(agent: str, events: list, event_types: dict, has_error: bool, duration: float | None) -> str:
    top = sorted(event_types.items(), key=lambda x: x[1], reverse=True)[:3]
    top_str = ", ".join(f"{et} ({n}x)" for et, n in top)
    dur = f"{duration:.0f}s" if duration else "unknown duration"
    error_note = " Errors were detected." if has_error else ""
    return (
        f"Session with agent '{agent}': {len(events)} events over {dur}. "
        f"Most frequent: {top_str}.{error_note}"
    )

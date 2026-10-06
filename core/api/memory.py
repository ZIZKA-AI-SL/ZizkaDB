"""
Memory API — three endpoints that make ZizkaDB easy to use as a drop-in
replacement for LLM-provided memory:

  POST /v1/memory/context  →  get a prompt-ready context block
  GET  /v1/memory/diff     →  what changed after a session
  DELETE /v1/memory/forget →  GDPR forget by any metadata field
"""

from fastapi import APIRouter, Depends
from services.exceptions import bad_request, not_found, service_unavailable
from pydantic import BaseModel
from typing import Any
import asyncio
import hashlib
import json
import logging

from api.deps import get_tenant, assert_agent_allowed, is_unbound_api_key
from db.connection import get_pool, get_qdrant
from services.embeddings import generate_embedding
from services.entitlements import embeddings_enabled, is_self_hosted_deployment

router = APIRouter()
log = logging.getLogger(__name__)


# ─────────────────────────────────────────
# MODELS
# ─────────────────────────────────────────

class ContextRequest(BaseModel):
    agent: str
    task: str
    max_tokens: int = 2000
    session_id: str | None = None   # exclude current session from context
    recent_limit: int = 10          # how many recent events to always include
    semantic_limit: int = 10        # how many semantically relevant events


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
        SELECT event_id, agent_id, timestamp, event_type, data, session_id
        FROM events
        WHERE tenant_id = $1 AND agent_id = $2
          AND ($3::text IS NULL OR session_id::text != $3)
        ORDER BY timestamp DESC
        LIMIT $4
        """,
        tenant_id, body.agent, body.session_id, body.recent_limit,
    )

    # 2. Semantically relevant events via Qdrant
    semantic_rows = []
    if not embeddings_enabled():
        if is_self_hosted_deployment():
            raise bad_request(
                detail=(
                    "Semantic memory is disabled on this self-hosted install. "
                    "Set EMBEDDINGS_ENABLED=true in infra/.env, restart the API, "
                    "then add your embedding API key in Dashboard → Settings."
                ),
            )
        raise bad_request(detail="Semantic memory is disabled for this deployment.")
    embedding = await generate_embedding(body.task, tenant_id)
    if not embedding:
        raise bad_request(
            detail=(
                "Embedding generation failed. Configure embeddings in Dashboard → Settings "
                "(platform key or your OpenAI API key)."
            ),
        )
    try:
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        qdrant = get_qdrant()

        must = [
            FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
            FieldCondition(key="agent_id", match=MatchValue(value=body.agent)),
        ]
        results = await qdrant.search(
            collection_name="agent_events",
            query_vector=embedding,
            query_filter=Filter(must=must),
            limit=body.semantic_limit,
            with_payload=True,
        )

        if results:
            sem_ids = [r.id for r in results]
            score_map = {str(r.id): round(r.score, 3) for r in results}

            sem_rows = await pool.fetch(
                """
                SELECT event_id, agent_id, timestamp, event_type, data, session_id
                FROM events
                WHERE event_id = ANY($1::uuid[]) AND tenant_id = $2
                  AND ($3::text IS NULL OR session_id::text != $3)
                """,
                sem_ids, tenant_id, body.session_id,
            )

            for row in sem_rows:
                semantic_rows.append((row, score_map.get(str(row["event_id"]), 0.0)))
    except Exception as e:
        log.warning(f"Semantic search failed for context: {e}")

    # 3. Merge — deduplicate, recent first then semantic fills gaps
    # Recent events take priority: they provide temporal grounding. Semantic
    # results that overlap with recent are skipped to avoid duplication.
    seen_ids = set()
    merged = []

    for row in recent_rows:
        eid = str(row["event_id"])
        if eid not in seen_ids:
            seen_ids.add(eid)
            merged.append({"row": row, "source": "recent", "score": 0.0})

    for row, score in sorted(semantic_rows, key=lambda x: x[1], reverse=True):
        eid = str(row["event_id"])
        if eid not in seen_ids:
            seen_ids.add(eid)
            merged.append({"row": row, "source": "semantic", "score": score})

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
        source_label = "Relevant" if item["source"] == "semantic" else "Recent"
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

    # Events in this session
    rows = await pool.fetch(
        """
        SELECT event_id, agent_id, event_type, data, timestamp, parent_event_id
        FROM events
        WHERE tenant_id = $1 AND session_id = $2
        ORDER BY timestamp ASC
        """,
        tenant_id, session_id,
    )

    if not rows:
        raise not_found("Session not found")

    agent_id = rows[0]["agent_id"]
    # A scoped agent key may only diff sessions belonging to its own agent.
    await assert_agent_allowed(tenant, agent_id)
    events = []
    event_types: dict[str, int] = {}
    has_error = False

    for row in rows:
        if scoped_agent and row["agent_id"] != scoped_agent:
            continue
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
    tenant: dict = Depends(get_tenant),
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

    # Find matching event IDs
    rows = await pool.fetch(
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

    # Vectors first: if the search index can't be cleaned, keep the Postgres
    # rows so a retry can still find (and delete) the vectors. Deleting a
    # missing Qdrant point is a no-op, so retries are idempotent.
    vector_cleanup = await _delete_vectors(event_ids, tenant_id)
    if not vector_cleanup and embeddings_enabled():
        raise service_unavailable(
            "Search index unavailable; nothing was deleted. Retry forget shortly."
        )

    # PostgreSQL does not support aggregate functions in RETURNING clauses.
    # We already know the count from the SELECT above, so use that directly.
    await pool.execute(
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

import json
import logging
from typing import Literal

from fastapi import APIRouter, Depends
from services.exceptions import bad_request
from pydantic import BaseModel, Field
from qdrant_client.models import FieldCondition, Filter, MatchValue

from api.deps import assert_agent_allowed, get_tenant, is_unbound_api_key
from db.connection import get_pool, get_qdrant
from services.embeddings import generate_embedding
from services.entitlements import embeddings_enabled, is_self_hosted_deployment
from services.keyword_search import keyword_search, reciprocal_rank_fusion

router = APIRouter()
log = logging.getLogger(__name__)

SearchMode = Literal["auto", "keyword", "semantic", "hybrid"]


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    agent: str | None = None
    limit: int = Field(default=10, ge=1, le=100)
    # auto: hybrid when embeddings are on, keyword otherwise.
    mode: SearchMode = "auto"


def _embeddings_off_error() -> Exception:
    if is_self_hosted_deployment():
        return bad_request(
            "Semantic search is disabled on this self-hosted install. Use mode "
            "'keyword' (or 'auto'), or set EMBEDDINGS_ENABLED=true in infra/.env, "
            "restart the API, then configure embeddings in Dashboard → Settings."
        )
    return bad_request("Semantic search is disabled for this deployment.")


async def vector_search(
    tenant_id: str,
    query: str,
    *,
    agent: str | None,
    limit: int,
) -> list[tuple[str, float]] | None:
    """(event_id, score) best first, or None when no embedding could be made."""
    embedding = await generate_embedding(query, tenant_id)
    if not embedding:
        return None
    must = [FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))]
    if agent:
        must.append(FieldCondition(key="agent_id", match=MatchValue(value=agent)))
    results = await get_qdrant().search(
        collection_name="agent_events",
        query_vector=embedding,
        query_filter=Filter(must=must),
        limit=limit,
        with_payload=True,
    )
    return [(str(r.id), r.score) for r in results]


@router.post("")
async def semantic_search(
    body: SearchRequest,
    tenant: dict = Depends(get_tenant),
):
    tenant_id = tenant["tenant_id"]
    agent = body.agent or tenant.get("agent_id")
    if is_unbound_api_key(tenant) and not agent:
        raise bad_request(
            "agent is required when using an unassigned API key. "
            "Pass agent in the request body or log an event first to bind the key."
        )
    if agent:
        await assert_agent_allowed(tenant, agent)

    mode = body.mode
    if mode == "auto":
        mode = "hybrid" if embeddings_enabled() else "keyword"
    if mode in ("semantic", "hybrid") and not embeddings_enabled():
        raise _embeddings_off_error()

    warning = None
    keyword_hits: list = []
    vector_hits: list[tuple[str, float]] | None = []

    if mode in ("semantic", "hybrid"):
        vector_hits = await vector_search(
            tenant_id, body.query, agent=agent,
            limit=body.limit * (2 if mode == "hybrid" else 1),
        )
        if vector_hits is None:
            if body.mode == "semantic":
                raise bad_request(
                    "Embedding generation failed. Configure embeddings in Dashboard → Settings."
                )
            # auto / hybrid: still answer, from keywords alone.
            warning = "Embedding generation failed; results come from keyword search only."
            mode = "keyword"
            vector_hits = []

    if mode in ("keyword", "hybrid"):
        keyword_hits = await keyword_search(
            get_pool(), tenant_id, body.query, agent=agent,
            limit=body.limit * (2 if mode == "hybrid" else 1),
        )

    if mode == "keyword":
        scores = {str(r["event_id"]): s for r, s in keyword_hits}
    elif mode == "semantic":
        scores = dict(vector_hits)
    else:
        scores = reciprocal_rank_fusion(
            [eid for eid, _ in vector_hits],
            [str(r["event_id"]) for r, _ in keyword_hits],
        )

    ranked = sorted(scores, key=scores.get, reverse=True)[: body.limit]
    response = {"query": body.query, "mode": mode, "results": []}
    if warning:
        response["warning"] = warning
    if not ranked:
        return response

    rows = await get_pool().fetch(
        """
        SELECT event_id, agent_id, timestamp, event_type,
               data, parent_event_id, session_id, sequence_no
        FROM events
        WHERE event_id = ANY($1::uuid[]) AND tenant_id = $2
        """,
        ranked, tenant_id,
    )

    events = []
    for row in rows:
        data = row["data"]
        if isinstance(data, str):
            data = json.loads(data)
        events.append({
            "event_id": str(row["event_id"]),
            "agent": row["agent_id"],
            "timestamp": row["timestamp"].isoformat(),
            "event": row["event_type"],
            "data": dict(data),
            "parent_id": str(row["parent_event_id"]) if row["parent_event_id"] else None,
            "score": round(scores.get(str(row["event_id"]), 0), 6),
        })

    events.sort(key=lambda x: x["score"], reverse=True)
    response["results"] = events
    return response

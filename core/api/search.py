import json

from fastapi import APIRouter, Depends
from services.exceptions import bad_request
from pydantic import BaseModel, Field
from qdrant_client.models import FieldCondition, Filter, MatchValue

from api.deps import assert_agent_allowed, get_tenant, is_unbound_api_key
from db.connection import get_pool, get_qdrant
from services.embeddings import generate_embedding
from services.entitlements import embeddings_enabled, is_self_hosted_deployment

router = APIRouter()


class SearchRequest(BaseModel):
    query: str
    agent: str | None = None
    limit: int = Field(default=10, ge=1, le=100)


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

    if not embeddings_enabled():
        if is_self_hosted_deployment():
            raise bad_request(
                "Semantic search is disabled on this self-hosted install. "
                "Set EMBEDDINGS_ENABLED=true in infra/.env, restart the API, "
                "then add your embedding API key in Dashboard → Settings."
            )
        raise bad_request("Semantic search is disabled for this deployment.")

    embedding = await generate_embedding(body.query, tenant_id)
    if not embedding:
        raise bad_request(
            "Embedding generation failed. Configure embeddings in Dashboard → Settings "
            "(platform key or your OpenAI API key)."
        )

    qdrant = get_qdrant()

    if agent:
        query_filter = Filter(
            must=[
                FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
                FieldCondition(key="agent_id", match=MatchValue(value=agent)),
            ]
        )
    else:
        query_filter = Filter(
            must=[FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))]
        )

    results = await qdrant.search(
        collection_name="agent_events",
        query_vector=embedding,
        query_filter=query_filter,
        limit=body.limit,
        with_payload=True,
    )

    pool = get_pool()
    event_ids = [r.id for r in results]
    if not event_ids:
        return {"results": []}

    rows = await pool.fetch(
        """
        SELECT event_id, agent_id, timestamp, event_type,
               data, parent_event_id, session_id, sequence_no
        FROM events
        WHERE event_id = ANY($1::uuid[]) AND tenant_id = $2
        """,
        event_ids, tenant_id,
    )

    score_map = {str(r.id): r.score for r in results}
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
            "score": score_map.get(str(row["event_id"]), 0),
        })

    events.sort(key=lambda x: x["score"], reverse=True)
    return {"query": body.query, "results": events}

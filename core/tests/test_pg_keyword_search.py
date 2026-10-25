"""Keyword search and keyword-backed context_for, against a real Postgres."""

import pytest

from api.memory import ContextRequest, get_context
from services.event_write import write_event
from services.keyword_search import keyword_search, reciprocal_rank_fusion, to_tsquery_text

pytestmark = pytest.mark.postgres

AGENT = "bot"


async def _seed(pool) -> str:
    tenant_id = str(await pool.fetchval(
        "INSERT INTO tenants (name) VALUES ('acme') RETURNING tenant_id"
    ))
    for event, data in [
        ("user_message", {"text": "I want a refund for order 1234"}),
        ("tool_call", {"tool": "lookup_order", "order_id": 1234}),
        ("user_message", {"text": "What is the weather in Berlin?"}),
        ("decision", {"action": "refunded", "amount": 20}),
    ]:
        await write_event(tenant_id=tenant_id, agent=AGENT, event=event, data=data)
    return tenant_id


async def test_finds_words_numbers_and_prefixes(pg_db):
    tenant_id = await _seed(pg_db)
    hits = await keyword_search(pg_db, tenant_id, "refund", agent=AGENT, limit=10)
    texts = [str(r["data"]) for r, _ in hits]
    assert len(hits) == 2  # "refund" and, as a prefix, "refunded"
    assert any("refunded" in t for t in texts)

    by_number = await keyword_search(pg_db, tenant_id, "order 1234", agent=AGENT, limit=10)
    assert {r["event_type"] for r, _ in by_number} >= {"user_message", "tool_call"}


async def test_more_matching_terms_rank_higher(pg_db):
    tenant_id = await _seed(pg_db)
    hits = await keyword_search(pg_db, tenant_id, "refund order 1234", agent=AGENT, limit=10)
    assert "refund for order 1234" in str(hits[0][0]["data"])


async def test_operators_in_the_query_are_not_interpreted(pg_db):
    tenant_id = await _seed(pg_db)
    assert to_tsquery_text("!(&|:*'") is None
    assert await keyword_search(pg_db, tenant_id, "'); DROP TABLE events; --",
                                agent=AGENT, limit=5) is not None
    assert await pg_db.fetchval("SELECT COUNT(*) FROM events") == 4


async def test_index_is_used_by_the_query(pg_db):
    await _seed(pg_db)
    from services.keyword_search import FTS_DOCUMENT

    async with pg_db.acquire() as conn:
        await conn.execute("SET enable_seqscan = off")
        plan = "\n".join(r[0] for r in await conn.fetch(
            f"EXPLAIN SELECT 1 FROM events WHERE {FTS_DOCUMENT} @@ to_tsquery('simple', 'refund:*')"
        ))
    assert "idx_events_fts" in plan


async def test_context_for_works_without_embeddings(pg_db):
    tenant_id = await _seed(pg_db)
    result = await get_context(
        ContextRequest(agent=AGENT, task="customer wants refund", recent_limit=1),
        tenant={"tenant_id": tenant_id, "agent_id": AGENT},
    )
    sources = {s["source"] for s in result["sources"]}
    assert sources == {"recent", "keyword"}
    assert "refund" in result["context"]


def test_rrf_rewards_agreement():
    scores = reciprocal_rank_fusion(["a", "b", "c"], ["b", "d"])
    assert max(scores, key=scores.get) == "b"


async def test_existing_install_gets_the_index_built(pg_db):
    import db.connection as connection

    await pg_db.execute("DROP INDEX idx_events_fts")
    await connection._ensure_fts_index()
    assert await pg_db.fetchval(
        "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
        "WHERE c.relname = 'idx_events_fts'"
    ) is True

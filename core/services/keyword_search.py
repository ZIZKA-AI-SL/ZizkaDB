"""Keyword search over events with Postgres full-text search: no embeddings needed.

Self-hosted installs run with embeddings off by default, so search and
context_for() fall back to this. With embeddings on, /v1/search combines it
with vector search (hybrid, reciprocal rank fusion).

The document is the event type plus every string and number in ``data``,
with the language-neutral ``simple`` configuration (no stemming or stop words,
so it works the same for any language). The expression must stay identical to
the ``idx_events_fts`` index (schema.sql and init_db) or the index is not used.
"""

from __future__ import annotations

import re
from typing import Any

FTS_DOCUMENT = (
    "(to_tsvector('simple', event_type) || "
    "jsonb_to_tsvector('simple', data, '[\"string\", \"numeric\"]'))"
)

# Terms beyond this add little and make the query slower.
MAX_TERMS = 32
RRF_K = 60

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def to_tsquery_text(query: str) -> str | None:
    """Any term may match (OR), each as a prefix, so "refund" finds "refunded".

    Only word characters reach to_tsquery, so its operators cannot be injected.
    """
    terms = list(dict.fromkeys(w.lower() for w in _WORD_RE.findall(query)))[:MAX_TERMS]
    if not terms:
        return None
    return " | ".join(f"{t}:*" for t in terms)


async def keyword_search(
    pool,
    tenant_id: str,
    query: str,
    *,
    agent: str | None,
    limit: int,
    exclude_session: str | None = None,
) -> list[tuple[Any, float]]:
    """(row, score) pairs, best first. Rows carry the usual event columns."""
    tsquery = to_tsquery_text(query)
    if tsquery is None:
        return []
    rows = await pool.fetch(
        f"""
        SELECT event_id, agent_id, timestamp, event_type, data, parent_event_id,
               session_id, sequence_no,
               ts_rank_cd({FTS_DOCUMENT}, q) AS score
        FROM events, to_tsquery('simple', $3) AS q
        WHERE tenant_id = $1
          AND ($2::text IS NULL OR agent_id = $2)
          AND ($5::text IS NULL OR session_id::text != $5)
          AND {FTS_DOCUMENT} @@ q
        ORDER BY score DESC, timestamp DESC
        LIMIT $4
        """,
        tenant_id,
        agent,
        tsquery,
        limit,
        exclude_session,
    )
    return [(r, float(r["score"])) for r in rows]


def reciprocal_rank_fusion(*ranked_id_lists: list[str], k: int = RRF_K) -> dict[str, float]:
    """Merge best-first id lists: score = sum of 1 / (k + rank) over the lists."""
    scores: dict[str, float] = {}
    for ids in ranked_id_lists:
        for rank, event_id in enumerate(ids, start=1):
            scores[event_id] = scores.get(event_id, 0.0) + 1.0 / (k + rank)
    return scores

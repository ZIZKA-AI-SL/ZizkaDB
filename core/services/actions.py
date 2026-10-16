"""Which tool / which API an agent called.

Events say *that* a tool or API was called through ``event_type``
(``tool_call``, ``api_call`` ...). *Which* one lives in the payload:

- tools:  ``data.tool`` (or ``data.tool_name``)
- APIs:   ``data.api``, or ``data.method`` + ``data.endpoint``/``data.url`` on
          ``api_*`` / ``http_*`` events

This module is the single place that reads those keys in SQL, so stats,
baselines and reports all name actions the same way. The name is read from the
JSON payload at query time: no schema change, and existing events are covered.
"""

from __future__ import annotations

# Longest action name kept in an aggregate key.
_NAME_MAX = 120

_TOOL_NAME = "COALESCE(NULLIF(data->>'tool', ''), NULLIF(data->>'tool_name', ''))"

_API_NAME = """COALESCE(
    NULLIF(data->>'api', ''),
    CASE WHEN event_type ILIKE 'api%' OR event_type ILIKE 'http%' THEN
        NULLIF(BTRIM(CONCAT_WS(' ', data->>'method', COALESCE(data->>'endpoint', data->>'url'))), '')
    END
)"""

ACTION_KIND_SQL = f"CASE WHEN {_TOOL_NAME} IS NOT NULL THEN 'tool' ELSE 'api' END"
ACTION_NAME_SQL = f"LEFT(COALESCE({_TOOL_NAME}, {_API_NAME}), {_NAME_MAX})"

# Follow-up events (tool_end, tool_result, tool_error ...) carry the name of
# the call they belong to; they are not calls themselves.
_FOLLOW_UP = r"event_type ~* '(result|end|error|fail|failed|output|response)$'"

# Same error heuristic as services/reports.py, plus HTTP 4xx/5xx statuses.
_ACTION_ERROR = (
    "event_type ILIKE '%error%' OR event_type ILIKE '%fail%' OR data ? 'error' "
    r"OR data->>'status' ~ '^[45]\d\d$'"
)


async def action_breakdown(
    pool,
    tenant_id: str,
    agent_id: str,
    *,
    session_ids: list[str] | None = None,
    from_ts=None,
    to_ts=None,
    limit: int = 20,
) -> list[dict]:
    """Per-tool / per-API call counts and error rates for one agent.

    Scope with ``session_ids``, or a ``[from_ts, to_ts)`` window (either bound
    optional); with neither, the agent's whole history. Most-called first.
    ``share_pct`` is each action's share of all named calls in scope.
    """
    where = ["tenant_id = $1", "agent_id = $2"]
    params: list = [tenant_id, agent_id]
    if session_ids is not None:
        params.append(session_ids)
        where.append(f"session_id = ANY(${len(params)}::text[])")
    if from_ts is not None:
        params.append(from_ts)
        where.append(f"timestamp >= ${len(params)}")
    if to_ts is not None:
        params.append(to_ts)
        where.append(f"timestamp < ${len(params)}")
    params.append(limit)

    rows = await pool.fetch(
        f"""
        WITH named AS (
            SELECT
                {ACTION_KIND_SQL} AS kind,
                {ACTION_NAME_SQL} AS name,
                ({_FOLLOW_UP})    AS follow_up,
                ({_ACTION_ERROR}) AS is_error
            FROM events
            WHERE {' AND '.join(where)}
        )
        SELECT
            kind,
            name,
            COUNT(*) FILTER (WHERE NOT follow_up) AS calls,
            COUNT(*) FILTER (WHERE is_error)      AS errors
        FROM named
        WHERE name IS NOT NULL
        GROUP BY kind, name
        ORDER BY calls DESC, errors DESC, name
        LIMIT ${len(params)}
        """,
        *params,
    )

    total_calls = sum(r["calls"] for r in rows) or 1
    out = []
    for r in rows:
        calls, errors = r["calls"], r["errors"]
        # An error logged without its call (older integrations) still means
        # one attempt, so the rate never exceeds 100%.
        attempts = max(calls, errors)
        out.append({
            "kind": r["kind"],
            "name": r["name"],
            "calls": calls,
            "errors": errors,
            "error_rate_pct": round(errors * 100.0 / attempts, 2) if attempts else 0.0,
            "share_pct": round(calls * 100.0 / total_calls, 2),
        })
    return out


def action_distribution(actions: list[dict]) -> dict[str, float]:
    """``{"tool:lookup_order": 62.5, "api:GET api.stripe.com/v1/charges": 37.5}``.

    Same shape as ``event_distribution`` so the drift primitives
    (``biggest_changes``) work on it unchanged.
    """
    return {f"{a['kind']}:{a['name']}": a["share_pct"] for a in actions if a["calls"]}

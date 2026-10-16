"""Per-tool / per-API breakdown against a real Postgres (the SQL is the logic)."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from services.actions import action_breakdown, action_distribution

pytestmark = pytest.mark.postgres

AGENT = "bot"


async def _seed(pool, events: list[tuple]) -> str:
    """events: (event_type, data[, session_id[, timestamp]])."""
    tenant_id = str(await pool.fetchval(
        "INSERT INTO tenants (name) VALUES ('acme') RETURNING tenant_id"
    ))
    await pool.execute(
        "INSERT INTO agents (agent_id, tenant_id) VALUES ($1, $2)", AGENT, tenant_id
    )
    for event_type, data, *rest in events:
        session_id = rest[0] if rest else "s1"
        ts = rest[1] if len(rest) > 1 else datetime.now(timezone.utc).replace(tzinfo=None)
        await pool.execute(
            """
            INSERT INTO events (tenant_id, agent_id, event_type, data, session_id, checksum, timestamp)
            VALUES ($1, $2, $3, $4, $5, 'x', $6)
            """,
            tenant_id, AGENT, event_type, json.dumps(data), session_id, ts,
        )
    return tenant_id


async def test_counts_calls_and_errors_per_tool(pg_db):
    tenant_id = await _seed(pg_db, [
        ("user_message", {"text": "hi"}),
        ("tool_call", {"tool": "lookup_order"}),
        ("tool_result", {"tool": "lookup_order", "result": 1}),
        ("tool_call", {"tool": "lookup_order"}),
        ("tool_error", {"tool": "lookup_order", "error": "timeout"}),
        ("tool_call", {"tool": "refund"}),
        ("tool_call", {"tool_name": "refund"}),
        ("tool_call", {"args": {}}),  # unnamed: not in the breakdown
    ])

    by_name = {a["name"]: a for a in await action_breakdown(pg_db, tenant_id, AGENT)}

    assert set(by_name) == {"lookup_order", "refund"}
    assert by_name["lookup_order"] == {
        "kind": "tool", "name": "lookup_order", "calls": 2, "errors": 1,
        "error_rate_pct": 50.0, "share_pct": 50.0,
    }
    assert by_name["refund"]["calls"] == 2 and by_name["refund"]["errors"] == 0


async def test_langchain_and_mcp_follow_ups_are_not_counted_as_calls(pg_db):
    tenant_id = await _seed(pg_db, [
        ("tool_start", {"tool": "search"}),
        ("tool_end", {"tool": "search", "output": "x"}),
        ("mcp_tool_call", {"tool": "read_file"}),
        ("mcp_tool_result", {"tool": "read_file", "result": {}}),
        ("mcp_tool_error", {"tool": "read_file", "error": {"code": -1}}),
    ])

    by_name = {a["name"]: a for a in await action_breakdown(pg_db, tenant_id, AGENT)}

    assert (by_name["search"]["calls"], by_name["search"]["errors"]) == (1, 0)
    assert (by_name["read_file"]["calls"], by_name["read_file"]["errors"]) == (1, 1)


async def test_api_calls_are_named_and_http_errors_counted(pg_db):
    tenant_id = await _seed(pg_db, [
        ("api_call", {"api": "POST api.stripe.com/v1/refunds", "status": 200}),
        ("api_call", {"api": "POST api.stripe.com/v1/refunds", "status": 502}),
        ("api_call", {"method": "GET", "url": "https://x.test/orders", "status": 404}),
        ("llm_response", {"method": "GET", "url": "https://not-an-api-event"}),
    ])

    actions = await action_breakdown(pg_db, tenant_id, AGENT)
    by_name = {a["name"]: a for a in actions}

    assert {a["kind"] for a in actions} == {"api"}
    assert by_name["POST api.stripe.com/v1/refunds"]["calls"] == 2
    assert by_name["POST api.stripe.com/v1/refunds"]["error_rate_pct"] == 50.0
    assert by_name["GET https://x.test/orders"]["errors"] == 1
    assert len(by_name) == 2


async def test_scopes_by_session_and_time_window_and_tenant(pg_db):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    tenant_id = await _seed(pg_db, [
        ("tool_call", {"tool": "old"}, "s-old", now - timedelta(days=10)),
        ("tool_call", {"tool": "new"}, "s-new", now - timedelta(hours=1)),
    ])
    other = str(await pg_db.fetchval("INSERT INTO tenants (name) VALUES ('other') RETURNING tenant_id"))

    names = lambda rows: [a["name"] for a in rows]  # noqa: E731
    assert names(await action_breakdown(pg_db, tenant_id, AGENT, session_ids=["s-old"])) == ["old"]
    assert names(await action_breakdown(
        pg_db, tenant_id, AGENT, from_ts=now - timedelta(days=1), to_ts=now
    )) == ["new"]
    assert names(await action_breakdown(pg_db, tenant_id, AGENT, to_ts=now - timedelta(days=1))) == ["old"]
    assert await action_breakdown(pg_db, other, AGENT) == []


async def test_error_without_a_logged_call_never_exceeds_100_pct(pg_db):
    tenant_id = await _seed(pg_db, [("tool_error", {"tool": "flaky", "error": "x"})])
    (row,) = await action_breakdown(pg_db, tenant_id, AGENT)
    assert (row["calls"], row["errors"], row["error_rate_pct"]) == (0, 1, 100.0)
    assert action_distribution([row]) == {}


def test_action_distribution_keys_by_kind_and_name():
    assert action_distribution([
        {"kind": "tool", "name": "a", "calls": 3, "share_pct": 75.0},
        {"kind": "api", "name": "GET x", "calls": 1, "share_pct": 25.0},
    ]) == {"tool:a": 75.0, "api:GET x": 25.0}

"""Authorization regression tests for the production-readiness hardening.

Covers: agent-scoped API keys must not reach other agents' data via the
SDK-callable memory/why routes, and key-management routes are dashboard-only.
"""

import datetime
import inspect
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api import agents as agents_api
from api import memory as memory_api
from api import events as events_api
from api.deps import require_dashboard_session
from api.memory import ContextRequest, ForgetRequest, get_context, session_diff, forget
from api.events import why


FULL_TENANT = {"tenant_id": "t-1"}                       # unscoped key / dashboard
SCOPED_TENANT = {"tenant_id": "t-1", "agent_id": "mine"}  # agent-scoped key


# ── /v1/memory/context ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_context_rejects_cross_agent_scoped_key(monkeypatch):
    """A scoped key asking for another agent's context is 403 before any DB read."""
    monkeypatch.setattr("api.memory.get_pool", lambda: AsyncMock())
    body = ContextRequest(agent="other", task="anything")
    with pytest.raises(HTTPException) as exc:
        await get_context(body, tenant=SCOPED_TENANT)
    assert exc.value.status_code == 403


# ── /v1/memory/diff ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_diff_rejects_cross_agent_scoped_key(monkeypatch):
    """A scoped key diffing another agent's session sees no rows and gets 404."""
    pool = AsyncMock()
    pool.fetch.return_value = []  # SQL filter drops 'other' agent's rows
    monkeypatch.setattr("api.memory.get_pool", lambda: pool)
    with pytest.raises(HTTPException) as exc:
        await session_diff("sess-1", tenant=SCOPED_TENANT)
    assert exc.value.status_code == 404
    sql, *params = pool.fetch.await_args.args
    assert "agent_id = $3" in sql
    assert params[-1] == "mine"


# ── /v1/memory/forget ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_forget_constrains_query_to_scoped_agent(monkeypatch):
    """A scoped key's forget must filter the SELECT by its own agent_id."""
    pool = AsyncMock()
    pool.fetch.return_value = []  # no matches → early return, no delete
    monkeypatch.setattr("api.memory.get_pool", lambda: pool)
    await forget(ForgetRequest(filter_key="user_id", filter_value="u1"), tenant=SCOPED_TENANT)

    sql, *_ = pool.fetch.await_args.args
    assert "agent_id = $4" in sql
    assert pool.fetch.await_args.args[-1] == "mine"  # scoped agent bound as $4


@pytest.mark.asyncio
async def test_forget_full_key_stays_tenant_wide(monkeypatch):
    """An unscoped key passes NULL for the agent filter (tenant-wide forget)."""
    pool = AsyncMock()
    pool.fetch.return_value = []
    monkeypatch.setattr("api.memory.get_pool", lambda: pool)
    await forget(ForgetRequest(filter_key="user_id", filter_value="u1"), tenant=FULL_TENANT)
    assert pool.fetch.await_args.args[-1] is None  # $4 is NULL → no agent constraint


# ── /v1/events/{id}/why ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_why_anchors_on_scoped_agent(monkeypatch):
    """A scoped key's causal walk must bind its agent_id to the recursive anchor."""
    pool = AsyncMock()
    pool.fetch.return_value = []  # anchor filtered out → 404 below
    monkeypatch.setattr("api.events.get_pool", lambda: pool)
    with pytest.raises(HTTPException) as exc:
        await why("00000000-0000-0000-0000-000000000001", depth=10, tenant=SCOPED_TENANT)
    assert exc.value.status_code == 404

    sql, *rest = pool.fetch.await_args.args
    assert "agent_id = $4" in sql
    assert pool.fetch.await_args.args[-1] == "mine"


@pytest.mark.asyncio
async def test_why_recursive_branch_filters_scoped_agent(monkeypatch):
    """Parent walk in why() must apply the same agent_id bind as the anchor."""
    pool = AsyncMock()
    pool.fetch.return_value = []
    monkeypatch.setattr("api.events.get_pool", lambda: pool)
    with pytest.raises(HTTPException):
        await why("00000000-0000-0000-0000-000000000001", depth=10, tenant=SCOPED_TENANT)

    sql = pool.fetch.await_args.args[0]
    assert sql.count("agent_id = $4") >= 2


@pytest.mark.asyncio
async def test_why_asserts_on_anchor_not_deepest_parent(monkeypatch):
    """Scoped key must authorize against the anchor event, not the chain root."""
    import datetime

    pool = AsyncMock()
    anchor_id = "00000000-0000-0000-0000-000000000001"
    root_id = "00000000-0000-0000-0000-000000000002"
    ts = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
    row = {
        "event_id": None,
        "agent_id": "mine",
        "timestamp": ts,
        "event_type": "test",
        "data": {},
        "parent_event_id": None,
        "session_id": None,
        "sequence_no": 1,
        "depth": 0,
    }
    root = {**row, "event_id": root_id, "agent_id": "other", "depth": 2}
    anchor = {**row, "event_id": anchor_id, "agent_id": "mine", "depth": 0}
    pool.fetch.return_value = [root, anchor]
    monkeypatch.setattr("api.events.get_pool", lambda: pool)

    result = await why(anchor_id, depth=10, tenant=SCOPED_TENANT)
    assert result["event_id"] == anchor_id
    assert result["chain_length"] == 2


# ── /v1/agents (list) ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_agents_scoped_key_filters_to_bound_agent(monkeypatch):
    """Agent-scoped keys must not enumerate other agents on the tenant."""
    pool = AsyncMock()
    pool.fetch.return_value = [
        {
            "agent_id": "mine",
            "first_seen": datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc),
            "last_seen": datetime.datetime(2024, 1, 2, tzinfo=datetime.timezone.utc),
            "event_count": 3,
            "metadata": None,
            "api_key_count": 1,
        }
    ]
    monkeypatch.setattr("api.agents.get_pool", lambda: pool)

    result = await agents_api.list_agents(tenant=SCOPED_TENANT)
    assert len(result) == 1
    assert result[0]["agent"] == "mine"

    sql = pool.fetch.await_args.args[0]
    assert "a.agent_id = $2" in sql
    assert pool.fetch.await_args.args[-1] == "mine"


# ── key management is dashboard-only ──────────────────────────────────────────

def _auth_dependency(func):
    """The Depends(...) callable guarding a route handler's auth parameter."""
    for p in inspect.signature(func).parameters.values():
        dep = getattr(p.default, "dependency", None)
        if dep is not None:
            return dep
    return None


def test_key_management_routes_require_dashboard_session():
    """list/create/revoke api-keys must all reject API keys (JWT-only)."""
    for func in (
        agents_api.list_agent_api_keys,
        agents_api.create_agent_api_key,
        agents_api.revoke_agent_api_key,
    ):
        assert _auth_dependency(func) is require_dashboard_session, func.__name__


def test_suggestions_route_is_rate_limited():
    """The expensive AI endpoint uses shared RateLimiter instances."""
    assert hasattr(agents_api, "suggestions_refresh_limiter")
    assert hasattr(agents_api, "suggestions_rate_limiter")
    assert hasattr(agents_api, "_check_suggestions_rate")


# ── per-agent analytics routes: get_tenant + assert_agent_allowed ────────────
#
# /report, /token-usage, and /token-optimization all use `get_tenant` (SDK-
# callable, accepts API keys + JWTs) rather than `require_dashboard_session`,
# then call `assert_agent_allowed(tenant, agent_id)` inline in the function
# body (not via Depends()) to enforce agent-scoped-key isolation — matching
# core/CLAUDE.md's documented auth decision tree for per-agent analytics
# routes. `_auth_dependency()` only detects Depends()-wired params, so the
# `assert_agent_allowed` call itself is verified via source inspection here
# (there was no existing test asserting this for any of these three routes
# before this change — a pre-existing gap, closed for all three at once).


def _calls_assert_agent_allowed(func) -> bool:
    return "assert_agent_allowed(tenant" in inspect.getsource(func)


def test_report_route_requires_tenant_and_agent_scope():
    src = inspect.getsource(agents_api.agent_report)
    assert "Depends(get_tenant)" in src
    assert _calls_assert_agent_allowed(agents_api.agent_report)


def test_token_usage_route_requires_tenant_and_agent_scope():
    src = inspect.getsource(agents_api.agent_token_usage)
    assert "Depends(get_tenant)" in src
    assert _calls_assert_agent_allowed(agents_api.agent_token_usage)


def test_token_optimization_route_requires_tenant_and_agent_scope():
    src = inspect.getsource(agents_api.agent_token_optimization)
    assert "Depends(get_tenant)" in src
    assert _calls_assert_agent_allowed(agents_api.agent_token_optimization)

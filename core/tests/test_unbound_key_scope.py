"""Regression tests: dev-key ENV gate and unassigned-key tenant isolation.

An API key with no agent yet (e.g. the key issued at signup) must not get
tenant-wide access, and reads must never bind a key to an arbitrary row's agent.
"""

import datetime
import importlib
import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api import deps
from api.agents import list_agents
from api.events import why
from api.memory import ForgetRequest, forget
from api.sessions import list_sessions, session_events, session_why

FULL_TENANT = {"tenant_id": "t-1"}  # dashboard JWT
UNBOUND_TENANT = {"tenant_id": "t-1", "key_id": "k-1", "agent_id": None}
SCOPED_TENANT = {"tenant_id": "t-1", "key_id": "k-1", "agent_id": "mine"}

NOW = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)


def _event(agent, depth=0, parent=None, event_id=None):
    return {
        "event_id": event_id or uuid.uuid4(),
        "agent_id": agent,
        "timestamp": NOW,
        "event_type": "action",
        "data": "{}",
        "parent_event_id": parent,
        "session_id": "s-1",
        "sequence_no": depth,
        "metadata": "{}",
        "depth": depth,
    }


@pytest.fixture
def claim(monkeypatch):
    """Record key-binding attempts; the claim succeeds for the requested agent."""
    mock = AsyncMock(side_effect=lambda **kw: kw["agent_id"])
    monkeypatch.setattr("services.api_keys.claim_unassigned_api_key", mock)
    return mock


# ── Dev-key ENV gate ──────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("env", "accepted"),
    [
        (None, True),
        ("development", True),
        (" Development ", True),
        ("production", False),
        ("staging", False),
        ("prod", False),
        ("test", False),
    ],
)
def test_dev_key_only_in_development(monkeypatch, env, accepted):
    if env is None:
        monkeypatch.delenv("ENV", raising=False)
    else:
        monkeypatch.setenv("ENV", env)
    monkeypatch.delenv("DEV_API_KEY", raising=False)
    try:
        mod = importlib.reload(deps)
        assert mod._dev_key_accepted("zizkadb_dev_local") is accepted
        assert mod.dev_keys_enabled() is accepted
    finally:
        monkeypatch.delenv("ENV", raising=False)
        importlib.reload(deps)


def test_is_unbound_api_key():
    assert deps.is_unbound_api_key(UNBOUND_TENANT)
    assert not deps.is_unbound_api_key(SCOPED_TENANT)
    assert not deps.is_unbound_api_key(FULL_TENANT)


# ── GET /v1/agents ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_agents_unbound_key_returns_nothing(monkeypatch):
    pool = AsyncMock()
    monkeypatch.setattr("api.agents.get_pool", lambda: pool)
    assert await list_agents(tenant=dict(UNBOUND_TENANT)) == []
    pool.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_agents_full_and_scoped(monkeypatch):
    pool = AsyncMock()
    pool.fetch.return_value = []
    monkeypatch.setattr("api.agents.get_pool", lambda: pool)
    await list_agents(tenant=FULL_TENANT)
    assert pool.fetch.await_args.args[1:] == ("t-1",)
    await list_agents(tenant=dict(SCOPED_TENANT))
    assert pool.fetch.await_args.args[1:] == ("t-1", "mine")


# ── GET /v1/sessions ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_sessions_unbound_without_agent_is_400(monkeypatch, claim):
    pool = AsyncMock()
    monkeypatch.setattr("api.sessions.get_pool", lambda: pool)
    with pytest.raises(HTTPException) as exc:
        await list_sessions(limit=20, agent=None, tenant=dict(UNBOUND_TENANT))
    assert exc.value.status_code == 400
    pool.fetch.assert_not_awaited()
    claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_sessions_unbound_with_agent_binds_and_filters(monkeypatch, claim):
    pool = AsyncMock()
    pool.fetch.return_value = []
    monkeypatch.setattr("api.sessions.get_pool", lambda: pool)
    tenant = dict(UNBOUND_TENANT)
    await list_sessions(limit=20, agent="a", tenant=tenant)
    assert claim.await_args.kwargs["agent_id"] == "a"
    assert tenant["agent_id"] == "a"
    assert "a" in pool.fetch.await_args.args


@pytest.mark.asyncio
async def test_list_sessions_scoped_key_other_agent_is_403(monkeypatch):
    monkeypatch.setattr("api.sessions.get_pool", lambda: AsyncMock())
    with pytest.raises(HTTPException) as exc:
        await list_sessions(limit=20, agent="other", tenant=dict(SCOPED_TENANT))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_list_sessions_dashboard_is_tenant_wide(monkeypatch):
    pool = AsyncMock()
    pool.fetch.return_value = []
    monkeypatch.setattr("api.sessions.get_pool", lambda: pool)
    await list_sessions(limit=20, agent=None, tenant=FULL_TENANT)
    assert pool.fetch.await_args.args[1:] == ("t-1", 20)


# ── DELETE /v1/memory/forget ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_forget_unbound_key_is_400_and_deletes_nothing(monkeypatch):
    pool = AsyncMock()
    monkeypatch.setattr("api.memory.get_pool", lambda: pool)
    with pytest.raises(HTTPException) as exc:
        await forget(
            ForgetRequest(filter_key="user_id", filter_value="u1"),
            tenant=dict(UNBOUND_TENANT),
        )
    assert exc.value.status_code == 400
    pool.fetch.assert_not_awaited()
    pool.execute.assert_not_awaited()


# ── GET /v1/sessions/{id}/events ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_session_events_unbound_without_agent_is_400(monkeypatch, claim):
    pool = AsyncMock()
    monkeypatch.setattr("api.sessions.get_pool", lambda: pool)
    with pytest.raises(HTTPException) as exc:
        await session_events("s-1", limit=500, agent=None, tenant=dict(UNBOUND_TENANT))
    assert exc.value.status_code == 400
    pool.fetch.assert_not_awaited()
    claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_session_events_never_binds_key_to_row_agent(monkeypatch, claim):
    pool = AsyncMock()
    pool.fetch.return_value = [_event("a")]
    monkeypatch.setattr("api.sessions.get_pool", lambda: pool)
    tenant = dict(UNBOUND_TENANT)
    await session_events("s-1", limit=500, agent="a", tenant=tenant)
    # Only the explicitly requested agent is claimed — exactly once.
    assert claim.await_count == 1
    assert claim.await_args.kwargs["agent_id"] == "a"
    sql = pool.fetch.await_args.args[0]
    assert "agent_id = $3" in sql


@pytest.mark.asyncio
async def test_session_events_dashboard_sees_multi_agent_session(monkeypatch, claim):
    pool = AsyncMock()
    pool.fetch.return_value = [_event("a"), _event("b")]
    monkeypatch.setattr("api.sessions.get_pool", lambda: pool)
    out = await session_events("s-1", limit=500, agent=None, tenant=FULL_TENANT)
    assert {e["agent"] for e in out["events"]} == {"a", "b"}
    claim.assert_not_awaited()


# ── why chains stay inside the key's agent ────────────────────────────────────

@pytest.mark.asyncio
async def test_session_why_filters_chain_by_scoped_agent(monkeypatch):
    anchor_id = uuid.uuid4()
    pool = AsyncMock()
    pool.fetchrow.return_value = {
        "event_id": anchor_id,
        "agent_id": "mine",
        "event_type": "action",
        "parent_event_id": uuid.uuid4(),
        "session_id": "s-1",
    }
    pool.fetch.return_value = [_event("mine", event_id=anchor_id)]
    monkeypatch.setattr("api.sessions.get_pool", lambda: pool)
    out = await session_why("s-1", str(anchor_id), depth=10, tenant=dict(SCOPED_TENANT))
    sql, *args = pool.fetch.await_args.args
    assert "e.agent_id = $4" in sql
    assert args[-1] == "mine"
    assert out["chain_length"] == 1


@pytest.mark.asyncio
async def test_why_unbound_key_drops_foreign_ancestors(monkeypatch, claim):
    anchor_id = uuid.uuid4()
    rows = [  # ordered depth DESC like the query
        _event("other", depth=2),
        _event("a", depth=1),
        _event("a", depth=0, event_id=anchor_id),
    ]
    pool = AsyncMock()
    pool.fetch.return_value = rows
    monkeypatch.setattr("api.events.get_pool", lambda: pool)
    out = await why(str(anchor_id), depth=10, tenant=dict(UNBOUND_TENANT))
    assert out["chain_length"] == 2
    assert {e["agent"] for e in out["chain"]} == {"a"}
    assert out["chain"][-1]["event_id"] == str(anchor_id)

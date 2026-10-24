"""One API key = one agent (lazy bind on first use).

Infrastructure-free: claim SQL is exercised against a fake asyncpg connection.
"""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.deps import _AGENT_KEY_MISMATCH, assert_agent_allowed
from services import api_keys


class _FakeConn:
    def __init__(self, *, bound_agent=None, revoked=False, tenant_ok=True):
        self.bound_agent = bound_agent
        self.revoked = revoked
        self.tenant_ok = tenant_ok
        self.update_sql = None
        self.update_args = None
        self.update_count = 0

    async def execute(self, sql, *args):
        if "UPDATE api_keys" in sql:
            self.update_sql = sql
            self.update_args = args
            self.update_count += 1
            if self.tenant_ok and not self.revoked and self.bound_agent is None:
                self.bound_agent = args[0]
                return "UPDATE 1"
            return "UPDATE 0"
        return "INSERT 0 0"

    async def fetchrow(self, sql, *args):
        if self.revoked or not self.tenant_ok:
            return None
        return {"agent_id": self.bound_agent}


class _FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return None


@pytest.mark.asyncio
async def test_claim_binds_unassigned_key():
    conn = _FakeConn()
    bound = await api_keys.claim_unassigned_api_key(
        key_id="00000000-0000-0000-0000-0000000000aa",
        tenant_id="00000000-0000-0000-0000-0000000000bb",
        agent_id="agent-a",
        pool=_FakePool(conn),
    )
    assert bound == "agent-a"
    assert conn.update_count == 1
    assert "agent_id IS NULL" in conn.update_sql
    assert "revoked = FALSE" in conn.update_sql
    assert "tenant_id" in conn.update_sql
    assert conn.update_args[0] == "agent-a"
    assert conn.update_args[1] == "00000000-0000-0000-0000-0000000000aa"
    assert conn.update_args[2] == "00000000-0000-0000-0000-0000000000bb"


@pytest.mark.asyncio
async def test_claim_loser_sees_winner_agent():
    conn = _FakeConn(bound_agent="agent-a")
    bound = await api_keys.claim_unassigned_api_key(
        key_id="00000000-0000-0000-0000-0000000000aa",
        tenant_id="00000000-0000-0000-0000-0000000000bb",
        agent_id="agent-b",
        pool=_FakePool(conn),
    )
    assert bound == "agent-a"
    assert conn.update_count == 1


@pytest.mark.asyncio
async def test_claim_revoked_returns_none():
    conn = _FakeConn(revoked=True)
    bound = await api_keys.claim_unassigned_api_key(
        key_id="00000000-0000-0000-0000-0000000000aa",
        tenant_id="00000000-0000-0000-0000-0000000000bb",
        agent_id="agent-a",
        pool=_FakePool(conn),
    )
    assert bound is None


@pytest.mark.asyncio
async def test_assert_binds_then_allows_same_agent(monkeypatch):
    claim = AsyncMock(return_value="agent-a")
    monkeypatch.setattr("services.api_keys.claim_unassigned_api_key", claim)
    tenant = {"tenant_id": "t-1", "key_id": "k-1", "agent_id": None}
    await assert_agent_allowed(tenant, "agent-a")
    claim.assert_awaited_once()
    assert tenant["agent_id"] == "agent-a"
    claim.reset_mock()
    await assert_agent_allowed(tenant, "agent-a")
    claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_assert_rejects_other_agent_after_bind(monkeypatch):
    monkeypatch.setattr(
        "services.api_keys.claim_unassigned_api_key",
        AsyncMock(return_value="agent-a"),
    )
    tenant = {"tenant_id": "t-1", "key_id": "k-1", "agent_id": None}
    await assert_agent_allowed(tenant, "agent-a")
    with pytest.raises(HTTPException) as exc:
        await assert_agent_allowed(tenant, "agent-b")
    assert exc.value.status_code == 403
    assert exc.value.detail == _AGENT_KEY_MISMATCH


@pytest.mark.asyncio
async def test_assert_pre_scoped_key_rejects_other_agent(monkeypatch):
    claim = AsyncMock()
    monkeypatch.setattr("services.api_keys.claim_unassigned_api_key", claim)
    tenant = {"tenant_id": "t-1", "key_id": "k-1", "agent_id": "agent-a"}
    with pytest.raises(HTTPException) as exc:
        await assert_agent_allowed(tenant, "agent-b")
    assert exc.value.status_code == 403
    claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_jwt_session_does_not_claim(monkeypatch):
    claim = AsyncMock()
    monkeypatch.setattr("services.api_keys.claim_unassigned_api_key", claim)
    tenant = {"tenant_id": "t-1", "user_id": "u-1"}
    await assert_agent_allowed(tenant, "agent-a")
    await assert_agent_allowed(tenant, "agent-b")
    claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_dev_tenant_does_not_claim(monkeypatch):
    claim = AsyncMock()
    monkeypatch.setattr("services.api_keys.claim_unassigned_api_key", claim)
    tenant = {
        "tenant_id": "00000000-0000-0000-0000-000000000001",
        "user_id": "00000000-0000-0000-0000-000000000001",
    }
    await assert_agent_allowed(tenant, "any-agent")
    claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_second_agent_is_403(monkeypatch):
    monkeypatch.setattr(
        "services.api_keys.claim_unassigned_api_key",
        AsyncMock(return_value="agent-a"),
    )
    tenant = {"tenant_id": "t-1", "key_id": "k-1", "agent_id": None}
    with pytest.raises(HTTPException) as exc:
        await assert_agent_allowed(tenant, "agent-b")
    assert exc.value.status_code == 403
    assert "another agent" in exc.value.detail


@pytest.mark.asyncio
async def test_claim_none_is_unauthorized(monkeypatch):
    monkeypatch.setattr(
        "services.api_keys.claim_unassigned_api_key",
        AsyncMock(return_value=None),
    )
    tenant = {"tenant_id": "t-1", "key_id": "k-1", "agent_id": None}
    with pytest.raises(HTTPException) as exc:
        await assert_agent_allowed(tenant, "agent-a")
    assert exc.value.status_code == 401


class _NamePool:
    def __init__(self, *, exists):
        self.exists = exists
        self.acquired = False

    async def fetchval(self, sql, *args):
        return 1 if self.exists else None

    def acquire(self):
        self.acquired = True
        raise AssertionError("must not touch api_keys or agents")


@pytest.mark.asyncio
async def test_claim_rejects_a_new_name_no_agent_route_can_reach():
    pool = _NamePool(exists=False)

    with pytest.raises(HTTPException) as exc:
        await api_keys.claim_unassigned_api_key(
            key_id="k", tenant_id="t", agent_id="my bot", pool=pool
        )

    assert exc.value.status_code == 400
    assert not pool.acquired


@pytest.mark.asyncio
async def test_claim_does_not_look_up_a_valid_name(monkeypatch):
    calls = []

    class _Pool(_NamePool):
        async def fetchval(self, sql, *args):
            calls.append(sql)
            raise AssertionError("stop here")

    monkeypatch.setattr("services.agent_limits.reserve_agent_slot", AsyncMock(side_effect=RuntimeError("reserved")))
    with pytest.raises(RuntimeError, match="reserved"):
        await api_keys.claim_unassigned_api_key(
            key_id="k", tenant_id="t", agent_id="my-bot", pool=_Pool(exists=False)
        )
    assert calls == []

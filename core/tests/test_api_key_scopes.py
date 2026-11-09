"""Tests for API key scope enforcement.

Tests that require_scope() correctly allows/rejects API keys based on their
stored scope string, with support for comma-separated scopes and legacy
values (read_write, read_only, admin).
"""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.deps import require_scope


def _tenant(scope: str | None, key_id: str | None = "k-1") -> dict:
    return {"tenant_id": "t-1", "key_id": key_id, "scope": scope, "agent_id": None}


class _TenantNoKey:
    """JWT dashboard session (no key_id) — always bypasses scope checks."""
    def __init__(self):
        self.tenant_id = "t-1"
        self.key_id = None
        self.scope = None
        self.agent_id = None


def _get_checker(*required_scopes: str):
    """Get the inner FastAPI dependency checker from require_scope()."""
    return require_scope(*required_scopes)


@pytest.mark.asyncio
async def test_require_scope_allows_matching_scope():
    """Key with matching scope passes."""
    checker = _get_checker("events:write")
    tenant = _tenant("events:read,events:write")
    result = await checker(tenant)
    assert result == tenant


@pytest.mark.asyncio
async def test_require_scope_allows_all_required_scopes():
    """Key with all required scopes passes."""
    checker = _get_checker("events:read", "events:write")
    tenant = _tenant("events:read,events:write")
    result = await checker(tenant)
    assert result == tenant


@pytest.mark.asyncio
async def test_require_scope_rejects_missing_scope():
    """Key missing a required scope raises 403."""
    checker = _get_checker("events:write")
    tenant = _tenant("events:read")
    with pytest.raises(HTTPException) as exc:
        await checker(tenant)
    assert exc.value.status_code == 403
    assert "events:write" in exc.value.detail


@pytest.mark.asyncio
async def test_require_scope_rejects_all_missing():
    """Key with none of the required scopes raises 403 listing all missing."""
    checker = _get_checker("events:write", "agents:read")
    tenant = _tenant("events:read")
    with pytest.raises(HTTPException) as exc:
        await checker(tenant)
    assert exc.value.status_code == 403
    assert "agents:read" in exc.value.detail
    assert "events:write" in exc.value.detail


@pytest.mark.asyncio
async def test_require_scope_allows_admin():
    """Key with admin scope passes for any required scope."""
    checker = _get_checker("events:write", "agents:manage", "webhooks:manage")
    tenant = _tenant("admin")
    result = await checker(tenant)
    assert result == tenant


@pytest.mark.asyncio
async def test_require_scope_expands_legacy_read_write():
    """Legacy read_write scope grants events:read and events:write."""
    checker = _get_checker("events:write")
    tenant = _tenant("read_write")
    result = await checker(tenant)
    assert result == tenant


@pytest.mark.asyncio
async def test_require_scope_expands_legacy_read_only():
    """Legacy read_only scope grants events:read but not events:write."""
    checker_read = _get_checker("events:read")
    tenant = _tenant("read_only")
    result = await checker_read(tenant)
    assert result == tenant

    checker_write = _get_checker("events:write")
    with pytest.raises(HTTPException) as exc:
        await checker_write(tenant)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_scope_allows_jwt_session():
    """JWT dashboard session (no key_id) always bypasses scope checks."""
    checker = _get_checker("events:write")
    tenant = {"tenant_id": "t-1", "key_id": None, "user_id": "u-1"}
    result = await checker(tenant)
    assert result == tenant


@pytest.mark.asyncio
async def test_require_scope_allows_dev_key():
    """Dev key (no key_id) always bypasses scope checks."""
    checker = _get_checker("events:write")
    tenant = _tenant(None, key_id=None)
    result = await checker(tenant)
    assert result == tenant


@pytest.mark.asyncio
async def test_require_scope_empty_scope_string():
    """Empty scope string grants nothing."""
    checker = _get_checker("events:read")
    tenant = _tenant("")
    with pytest.raises(HTTPException) as exc:
        await checker(tenant)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_scope_none_scope():
    """None scope grants nothing."""
    checker = _get_checker("events:read")
    tenant = _tenant(None)
    with pytest.raises(HTTPException) as exc:
        await checker(tenant)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_scope_unknown_scope_name():
    """Unknown scope names are passed through (forward-compatible)."""
    checker = _get_checker("future:scope")
    tenant = _tenant("future:scope")
    result = await checker(tenant)
    assert result == tenant


@pytest.mark.asyncio
async def test_require_scope_comma_separated_multiple():
    """Multiple comma-separated scopes work."""
    checker = _get_checker("events:read")
    tenant = _tenant("agents:read,events:read,webhooks:read")
    result = await checker(tenant)
    assert result == tenant
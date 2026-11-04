"""Regression tests for POST /v1/memory/context request limits.

Before the fix, ContextRequest declared max_tokens / recent_limit /
semantic_limit as plain ints with no bounds, unlike every other limit in
the codebase (SearchRequest uses Field(ge=1, le=100); list routes use
Query(ge=..., le=...)). A negative recent_limit was forwarded verbatim as
the Postgres ``LIMIT $4`` parameter, which Postgres rejects ("LIMIT must
not be negative") — an unhandled error surfacing as a 500. Zero or
negative max_tokens silently produced an empty context, and an unbounded
recent_limit/semantic_limit allowed an arbitrarily large fetch.

Invalid values must now be rejected at request validation (422) before
any database or vector-store call, mirroring SearchRequest's bounds.
"""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.deps import get_tenant
from api.memory import ContextRequest, get_context
from main import app

client = TestClient(app, raise_server_exceptions=False)

_TENANT = {"tenant_id": "11111111-1111-1111-1111-111111111111"}


def _override_tenant():
    return _TENANT


# ── Model level ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "kwargs",
    [
        {"recent_limit": -1},
        {"recent_limit": 0},
        {"recent_limit": 101},
        {"recent_limit": 1_000_000},
        {"semantic_limit": -1},
        {"semantic_limit": 0},
        {"semantic_limit": 101},
        {"max_tokens": 0},
        {"max_tokens": -2000},
    ],
)
def test_out_of_range_limits_are_rejected(kwargs):
    with pytest.raises(ValidationError):
        ContextRequest(agent="agent1", task="anything", **kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"recent_limit": 1},
        {"recent_limit": 100},
        {"semantic_limit": 1},
        {"semantic_limit": 100},
        {"max_tokens": 1},
    ],
)
def test_in_range_limits_are_accepted(kwargs):
    body = ContextRequest(agent="agent1", task="anything", **kwargs)
    assert body.recent_limit >= 1
    assert body.semantic_limit >= 1
    assert body.max_tokens >= 1


def test_defaults_are_unchanged():
    body = ContextRequest(agent="agent1", task="anything")
    assert body.max_tokens == 2000
    assert body.recent_limit == 10
    assert body.semantic_limit == 10


# ── Handler level ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_negative_recent_limit_never_reaches_the_pool(monkeypatch):
    """The pre-fix handler passed recent_limit=-1 straight into LIMIT $4."""
    pool = AsyncMock()
    monkeypatch.setattr("api.memory.get_pool", lambda: pool)
    with pytest.raises(ValidationError):
        ContextRequest(agent="agent1", task="anything", recent_limit=-1)
    pool.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_valid_recent_limit_is_forwarded_to_the_query(monkeypatch):
    pool = AsyncMock()
    pool.fetch.return_value = []
    monkeypatch.setattr("api.memory.get_pool", lambda: pool)
    # No vectors, no keyword hits: only the recent-events fetch runs, and it
    # must carry the requested limit as its last argument.
    monkeypatch.setattr("api.memory.vector_search", AsyncMock(return_value=None))
    monkeypatch.setattr("api.memory.keyword_search", AsyncMock(return_value=[]))

    body = ContextRequest(agent="agent1", task="anything", recent_limit=7)
    await get_context(body, tenant=_TENANT)
    assert pool.fetch.await_args_list[0].args[-1] == 7


# ── Route level ───────────────────────────────────────────────────────────────


class TestContextRouteValidation:
    def setup_method(self):
        app.dependency_overrides[get_tenant] = _override_tenant

    def teardown_method(self):
        app.dependency_overrides.pop(get_tenant, None)

    @pytest.mark.parametrize(
        "payload",
        [
            {"agent": "agent1", "task": "x", "recent_limit": -1},
            {"agent": "agent1", "task": "x", "recent_limit": 0},
            {"agent": "agent1", "task": "x", "semantic_limit": -5},
            {"agent": "agent1", "task": "x", "max_tokens": 0},
        ],
    )
    def test_invalid_limits_return_422_not_500(self, monkeypatch, payload):
        pool = AsyncMock()
        pool.fetch.return_value = []
        monkeypatch.setattr("api.memory.get_pool", lambda: pool)
        response = client.post("/v1/memory/context", json=payload)
        assert response.status_code == 422
        pool.fetch.assert_not_awaited()

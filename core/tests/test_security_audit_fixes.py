"""Security regression tests verifying vulnerability audit fixes."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import bcrypt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api.auth import router as auth_router
from api.deps import require_dashboard_session
from api.memory import session_diff
from api.utils import client_ip
from middleware.request_id import RequestIdMiddleware, REQUEST_ID_HEADER
from main import app
from services.auth import verify_otp
from services.event_write import write_event
from services.rate_limiter import InMemoryStorage

client = TestClient(app)


# ──────────────────────────────────────────────────────────────────────────────
# 1. Memory Diff Scoped Agent & Unbound Isolation
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_diff_unbound_key_is_400():
    """Unbound API key calling session_diff must be rejected with 400."""
    unbound_tenant = {"tenant_id": "t-1", "key_id": "k-1"}
    with pytest.raises(HTTPException) as exc:
        await session_diff("sess-1", tenant=unbound_tenant)
    assert exc.value.status_code == 400
    assert "diff requires an agent-scoped API key" in exc.value.detail


@pytest.mark.asyncio
async def test_diff_scoped_agent_filters_out_other_agent_events(monkeypatch):
    """Scoped key only sees its own events in a multi-agent session."""
    pool = AsyncMock()
    now = datetime.now(timezone.utc)
    # Session has event from 'mine' and event from 'other'
    rows = [
        {
            "event_id": str(uuid4()),
            "agent_id": "mine",
            "event_type": "USER_QUERY",
            "data": {"q": "hello"},
            "timestamp": now,
            "parent_event_id": None,
        },
        {
            "event_id": str(uuid4()),
            "agent_id": "other",
            "event_type": "INTERNAL_TOOL",
            "data": {"secret": "confidential_other_data"},
            "timestamp": now,
            "parent_event_id": None,
        },
    ]
    pool.fetch.return_value = rows
    pool.fetchrow.return_value = None
    monkeypatch.setattr("api.memory.get_pool", lambda: pool)

    res = await session_diff("sess-1", tenant={"tenant_id": "t-1", "agent_id": "mine"})
    # 'other' agent's event must not be returned in top_events or event_count
    assert res["event_count"] == 1
    assert len(res["top_events"]) == 1
    assert res["top_events"][0]["event"] == "USER_QUERY"
    assert "confidential_other_data" not in str(res)


# ──────────────────────────────────────────────────────────────────────────────
# 2. Auth test-event requires Dashboard Session
# ──────────────────────────────────────────────────────────────────────────────

def test_auth_test_event_dependency_is_dashboard_session():
    """POST /v1/auth/test-event must use require_dashboard_session."""
    route = next(
        r for r in auth_router.routes
        if getattr(r, "path", None) == "/test-event" and "POST" in r.methods
    )
    # Ensure require_dashboard_session dependency is present
    dep_callables = [d.call for d in route.dependencies or []]
    # Check endpoint's parameter dependencies
    sig = route.endpoint.__annotations__
    assert sig.get("tenant") is not None
    assert route.endpoint.__defaults__[0].dependency == require_dashboard_session


# ──────────────────────────────────────────────────────────────────────────────
# 3. Client IP Trusted Proxy Validation
# ──────────────────────────────────────────────────────────────────────────────

def test_client_ip_untrusted_proxy_ignores_x_forwarded_for():
    """Untrusted peer IP cannot spoof X-Forwarded-For."""
    mock_request = MagicMock()
    mock_request.client.host = "203.0.113.50"
    mock_request.headers = {"x-forwarded-for": "10.0.0.1"}

    with patch.dict("os.environ", {"TRUSTED_PROXIES": "127.0.0.1"}):
        ip = client_ip(mock_request)
        assert ip == "203.0.113.50"


def test_client_ip_trusted_proxy_accepts_x_forwarded_for():
    """Trusted proxy IP allows extracting client IP from X-Forwarded-For."""
    mock_request = MagicMock()
    mock_request.client.host = "127.0.0.1"
    mock_request.headers = {"x-forwarded-for": "203.0.113.50, 10.0.0.1"}

    with patch.dict("os.environ", {"TRUSTED_PROXIES": "127.0.0.1"}):
        ip = client_ip(mock_request)
        assert ip == "203.0.113.50"


# ──────────────────────────────────────────────────────────────────────────────
# 4. OTP Attempt Counter & Lockout
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_otp_attempt_counter_burns_after_limit(monkeypatch):
    """5 failed attempts burn the OTP code and reject subsequent tries."""
    pool = AsyncMock()
    otp_hash = bcrypt.hashpw(b"123456", bcrypt.gensalt()).decode()
    row = {
        "otp_id": str(uuid4()),
        "otp_hash": otp_hash,
        "attempts": 4,  # Next failure will reach max_attempts (5)
        "max_attempts": 5,
    }
    pool.fetchrow.return_value = row
    monkeypatch.setattr("services.auth.get_pool", lambda: pool)

    # 5th failed attempt should burn OTP
    with pytest.raises(ValueError) as exc:
        await verify_otp("user@example.com", "999999")
    assert "Too many failed attempts" in str(exc.value)
    # Verify pool execute burned the OTP (used = TRUE)
    executed_sql = pool.execute.await_args.args[0]
    assert "used = TRUE" in executed_sql


# ──────────────────────────────────────────────────────────────────────────────
# 5. UUID Validation on write_event parent_id
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_write_event_malformed_parent_id_returns_400():
    """Malformed parent_id raises 400 Bad Request, not unhandled 500 DataError."""
    with pytest.raises(HTTPException) as exc:
        await write_event(
            tenant_id=str(uuid4()),
            agent="bot",
            event="test",
            data={},
            parent_id="not-a-valid-uuid-1234",
        )
    assert exc.value.status_code == 400
    assert "not a valid UUID" in exc.value.detail


# ──────────────────────────────────────────────────────────────────────────────
# 6. Request ID Sanitization
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_request_id_middleware_sanitizes_crlf():
    """RequestIdMiddleware strips/replaces CRLF and invalid characters."""
    middleware = RequestIdMiddleware(app)
    mock_request = MagicMock()
    mock_request.headers = {REQUEST_ID_HEADER: "req-1\r\n[FORGED_LOG] malicious"}
    mock_request.state = MagicMock()

    call_next = AsyncMock(return_value=MagicMock(headers={}))
    await middleware.dispatch(mock_request, call_next)

    # Malformed ID must be replaced with a valid UUID, not logged as injected CRLF
    assigned_id = mock_request.state.request_id
    assert "\r" not in assigned_id
    assert "\n" not in assigned_id
    assert "FORGED_LOG" not in assigned_id


# ──────────────────────────────────────────────────────────────────────────────
# 7. Community Image Upload Magic Bytes Validation
# ──────────────────────────────────────────────────────────────────────────────

def test_community_upload_magic_bytes_rejected():
    """File with .png extension but text content is rejected with 400."""
    res = client.post(
        "/v1/community/upload",
        files={"file": ("malicious.png", b"<script>alert(1)</script>", "image/png")},
    )
    assert res.status_code == 400
    assert "Invalid image file format" in res.json()["detail"]


def test_community_upload_valid_png_accepted(tmp_path, monkeypatch):
    """File with valid PNG signature is accepted."""
    monkeypatch.setattr("api.community.UPLOAD_DIR", tmp_path)
    # PNG signature: 89 50 4E 47 0D 0A 1A 0A + dummy bytes
    valid_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    res = client.post(
        "/v1/community/upload",
        files={"file": ("good.png", valid_png, "image/png")},
    )
    assert res.status_code == 200
    assert "url" in res.json()

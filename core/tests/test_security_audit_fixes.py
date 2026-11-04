"""Security regression tests verifying vulnerability audit fixes."""

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
from api.utils import client_ip, parse_trusted_proxies
from middleware.request_id import RequestIdMiddleware, REQUEST_ID_HEADER
from main import app
from services.auth import OTP_MAX_ATTEMPTS, verify_otp
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
async def test_diff_scoped_agent_filters_in_sql(monkeypatch):
    """Scoped key filters by agent in SQL, so other agents' rows never load."""
    pool = AsyncMock()
    now = datetime.now(timezone.utc)
    pool.fetch.return_value = [
        {
            "event_id": str(uuid4()),
            "agent_id": "mine",
            "event_type": "USER_QUERY",
            "data": {"q": "hello"},
            "timestamp": now,
            "parent_event_id": None,
        },
    ]
    pool.fetchrow.return_value = None
    monkeypatch.setattr("api.memory.get_pool", lambda: pool)

    res = await session_diff("sess-1", tenant={"tenant_id": "t-1", "agent_id": "mine"})

    sql, *params = pool.fetch.await_args.args
    assert "($3::text IS NULL OR agent_id = $3)" in sql
    assert params == ["t-1", "sess-1", "mine"]
    assert res["event_count"] == 1
    assert res["top_events"][0]["event"] == "USER_QUERY"


@pytest.mark.asyncio
async def test_diff_scoped_agent_not_first_in_session_is_allowed(monkeypatch):
    """Agent B may diff a session whose first event belongs to agent A."""
    pool = AsyncMock()
    # SQL already filtered out agent A's (earlier) rows.
    pool.fetch.return_value = [
        {
            "event_id": str(uuid4()),
            "agent_id": "agent-b",
            "event_type": "TOOL_CALL",
            "data": {},
            "timestamp": datetime.now(timezone.utc),
            "parent_event_id": None,
        },
    ]
    pool.fetchrow.return_value = None
    monkeypatch.setattr("api.memory.get_pool", lambda: pool)

    res = await session_diff(
        "sess-1", tenant={"tenant_id": "t-1", "agent_id": "agent-b"}
    )
    assert res["event_count"] == 1


@pytest.mark.asyncio
async def test_diff_scoped_agent_without_events_is_404(monkeypatch):
    """A session with no events for the scoped agent is reported as not found."""
    pool = AsyncMock()
    pool.fetch.return_value = []
    monkeypatch.setattr("api.memory.get_pool", lambda: pool)

    with pytest.raises(HTTPException) as exc:
        await session_diff("sess-1", tenant={"tenant_id": "t-1", "agent_id": "mine"})
    assert exc.value.status_code == 404


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

def _ip_request(peer: str, xff: str | None = None) -> MagicMock:
    request = MagicMock()
    request.client.host = peer
    request.headers = {"x-forwarded-for": xff} if xff is not None else {}
    return request


@pytest.fixture
def trusted_proxies(monkeypatch):
    def _set(raw: str) -> None:
        monkeypatch.setattr(
            "api.utils.TRUSTED_PROXIES", parse_trusted_proxies(raw)
        )
    return _set


def test_client_ip_untrusted_proxy_ignores_x_forwarded_for(trusted_proxies):
    """Untrusted peer IP cannot spoof X-Forwarded-For."""
    trusted_proxies("127.0.0.1")
    assert client_ip(_ip_request("203.0.113.50", "10.0.0.1")) == "203.0.113.50"


def test_client_ip_trusted_proxy_accepts_x_forwarded_for(trusted_proxies):
    """Trusted proxy IP allows extracting client IP from X-Forwarded-For."""
    trusted_proxies("127.0.0.1")
    assert client_ip(_ip_request("127.0.0.1", "203.0.113.50")) == "203.0.113.50"


def test_client_ip_spoofed_left_hop_is_ignored(trusted_proxies):
    """nginx appends the real IP; a client-supplied left-most value is ignored."""
    trusted_proxies("172.18.0.1")
    request = _ip_request("172.18.0.1", "6.6.6.6, 203.0.113.9")
    assert client_ip(request) == "203.0.113.9"


def test_client_ip_skips_multiple_trusted_hops(trusted_proxies):
    """Walk right-to-left past every trusted hop to the first untrusted one."""
    trusted_proxies("127.0.0.1,10.0.0.0/8")
    request = _ip_request("127.0.0.1", "6.6.6.6, 203.0.113.9, 10.1.2.3")
    assert client_ip(request) == "203.0.113.9"


def test_client_ip_default_trusts_docker_bridge(monkeypatch):
    """Default config trusts the Docker bridge gateway nginx traffic arrives from."""
    from api.utils import _DEFAULT_TRUSTED_PROXIES
    monkeypatch.setattr(
        "api.utils.TRUSTED_PROXIES", parse_trusted_proxies(_DEFAULT_TRUSTED_PROXIES)
    )
    assert client_ip(_ip_request("172.18.0.1", "203.0.113.9")) == "203.0.113.9"
    assert "testclient" not in _DEFAULT_TRUSTED_PROXIES


def test_client_ip_invalid_trusted_entries_are_skipped():
    """Non-IP entries (e.g. hostnames) are dropped rather than matched as strings."""
    networks = parse_trusted_proxies("127.0.0.1, testclient, ,10.0.0.0/8")
    assert [str(n) for n in networks] == ["127.0.0.1/32", "10.0.0.0/8"]


# ──────────────────────────────────────────────────────────────────────────────
# 4. OTP Attempt Counter & Lockout
# ──────────────────────────────────────────────────────────────────────────────

def _otp_pool(update_result):
    pool = AsyncMock()
    otp_hash = bcrypt.hashpw(b"123456", bcrypt.gensalt()).decode()
    pool.fetchrow.side_effect = [
        {"otp_id": "otp-1", "otp_hash": otp_hash},
        update_result,
    ]
    return pool


@pytest.mark.asyncio
async def test_otp_wrong_guess_increments_atomically(monkeypatch):
    """A wrong guess is counted with one atomic UPDATE, not read-modify-write."""
    pool = _otp_pool({"attempts": 1, "used": False})
    monkeypatch.setattr("services.auth.get_pool", lambda: pool)

    with pytest.raises(ValueError, match="Invalid OTP"):
        await verify_otp("user@example.com", "999999")

    sql, otp_id, max_attempts = pool.fetchrow.await_args.args
    assert "attempts = attempts + 1" in sql
    assert "used = FALSE" in sql
    assert (otp_id, max_attempts) == ("otp-1", OTP_MAX_ATTEMPTS)


@pytest.mark.asyncio
async def test_otp_attempt_counter_burns_after_limit(monkeypatch):
    """The guess that reaches the limit burns the code."""
    pool = _otp_pool({"attempts": OTP_MAX_ATTEMPTS, "used": True})
    monkeypatch.setattr("services.auth.get_pool", lambda: pool)

    with pytest.raises(ValueError, match="Too many failed attempts"):
        await verify_otp("user@example.com", "999999")


@pytest.mark.asyncio
async def test_otp_burned_by_concurrent_request(monkeypatch):
    """No row back means a concurrent request already burned the code."""
    pool = _otp_pool(None)
    monkeypatch.setattr("services.auth.get_pool", lambda: pool)

    with pytest.raises(ValueError, match="Too many failed attempts"):
        await verify_otp("user@example.com", "999999")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_otp_concurrent_wrong_guesses_burn_after_limit(monkeypatch):
    """N parallel wrong guesses against real Postgres burn the code after 5."""
    import asyncio
    import os

    import asyncpg

    dsn = os.getenv(
        "DATABASE_URL", "postgresql://zizkadb:zizkadb@localhost:5432/zizkadb"
    )
    pool = await asyncpg.create_pool(dsn, min_size=10, max_size=20)
    email = f"otp-race-{uuid4().hex}@example.com"
    otp_hash = bcrypt.hashpw(b"123456", bcrypt.gensalt(rounds=4)).decode()
    try:
        await pool.execute(
            """
            INSERT INTO auth_otps (email, otp_hash, expires_at)
            VALUES ($1, $2, NOW() + INTERVAL '15 minutes')
            """,
            email, otp_hash,
        )
        monkeypatch.setattr("services.auth.get_pool", lambda: pool)

        guesses = 20
        results = await asyncio.gather(
            *(verify_otp(email, "999999") for _ in range(guesses)),
            return_exceptions=True,
        )
        messages = [str(r) for r in results]
        assert all(isinstance(r, ValueError) for r in results)

        row = await pool.fetchrow(
            "SELECT attempts, used FROM auth_otps WHERE email = $1", email
        )
        assert row["used"] is True
        assert row["attempts"] == OTP_MAX_ATTEMPTS
        # Exactly OTP_MAX_ATTEMPTS - 1 guesses saw a plain "Invalid OTP".
        assert messages.count("Invalid OTP") == OTP_MAX_ATTEMPTS - 1

        # The correct code no longer works once burned.
        with pytest.raises(ValueError, match="expired or not found"):
            await verify_otp(email, "123456")
    finally:
        await pool.execute("DELETE FROM auth_otps WHERE email = $1", email)
        await pool.close()


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
    assert exc.value.detail == "parent_id must be a valid event UUID"


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

def test_community_upload_magic_bytes_rejected(tmp_path, monkeypatch):
    """File with .png extension but text content is rejected with 400."""
    monkeypatch.setattr("api.community.UPLOAD_DIR", tmp_path)
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


def test_community_media_serves_uploaded_file(tmp_path, monkeypatch):
    """A file named like an upload is served with hardening headers."""
    monkeypatch.setattr("api.community.UPLOAD_DIR", tmp_path)
    name = f"{uuid4().hex}.png"
    (tmp_path / name).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    res = client.get(f"/v1/community/media/{name}")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/png"
    assert res.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("name", ["..", "...", "x.png", f"{'a' * 32}.svg", f"{'A' * 32}.png"])
def test_community_media_rejects_non_upload_names(tmp_path, monkeypatch, name):
    """Only <32 hex>.<image ext> names are served; traversal-ish names 404."""
    monkeypatch.setattr("api.community.UPLOAD_DIR", tmp_path / "uploads")
    (tmp_path / "uploads").mkdir()
    res = client.get(f"/v1/community/media/{name}")
    assert res.status_code == 404

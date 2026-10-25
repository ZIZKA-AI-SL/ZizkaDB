import asyncio
import json

import httpx
import pytest

from zizkadb.client import ZizkaDB
from zizkadb.exceptions import AgentScopeError, AuthError, NotFoundError, RateLimitError, ZizkaDBError


def _prevent_telemetry(monkeypatch):
    """Stop telemetry from firing before/inside ZizkaDB.__init__."""
    monkeypatch.setattr("zizkadb.client._telemetry_ping", lambda mode, sdk="python": None)
    monkeypatch.setenv("ZIZKADB_TELEMETRY", "false")


def _client(monkeypatch, response_handler, api_key="zizkadb_live_test", host="https://example.test"):
    _prevent_telemetry(monkeypatch)
    db = ZizkaDB(api_key=api_key, host=host)
    db._client = httpx.AsyncClient(
        transport=httpx.MockTransport(response_handler),
        base_url=db._base_url,
        headers=db._headers(),
    )
    return db


async def _close(db):
    await db._client.aclose()


def test_log_posts_expected_event_payload(monkeypatch):
    captures = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captures["method"] = request.method
        captures["url"] = str(request.url)
        captures["auth"] = request.headers.get("authorization")
        captures["body"] = request.read().decode()
        return httpx.Response(
            200,
            json={
                "event_id": "00000000-0000-0000-0000-000000000001",
                "timestamp": "2026-06-19T00:00:00Z",
                "sequence_no": 1,
                "checksum": "abc123",
            },
        )

    db = _client(monkeypatch, handler)
    try:
        result = asyncio.run(
            db.log(
                agent="test-agent",
                event="tool_call",
                data={"tool": "search"},
                parent_id="parent-1",
                session_id="session-1",
            )
        )
    finally:
        asyncio.run(_close(db))

    assert captures["method"] == "POST"
    assert captures["url"] == "https://example.test/v1/events"
    assert captures["auth"] == "Bearer zizkadb_live_test"
    body_json = captures["body"]
    body = json.loads(body_json)
    assert body["agent"] == "test-agent"
    assert body["event"] == "tool_call"
    assert body["data"] == {"tool": "search"}
    assert body["parent_id"] == "parent-1"
    assert body["session_id"] == "session-1"
    assert body["metadata"] is None
    assert result.event_id == "00000000-0000-0000-0000-000000000001"
    assert result.sequence_no == 1


def test_log_sends_idempotency_key_only_when_given(monkeypatch):
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.read().decode()))
        return httpx.Response(200, json={
            "event_id": "00000000-0000-0000-0000-000000000001",
            "timestamp": "2026-06-19T00:00:00Z",
            "sequence_no": 1,
            "checksum": "abc123",
        })

    db = _client(monkeypatch, handler)
    try:
        asyncio.run(db.log(agent="a", event="e", data={}, idempotency_key="req-1"))
        asyncio.run(db.log(agent="a", event="e", data={}))
    finally:
        asyncio.run(_close(db))

    assert bodies[0]["idempotency_key"] == "req-1"
    assert "idempotency_key" not in bodies[1]


def test_log_prints_why_hint_on_localhost(monkeypatch, capsys):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "event_id": "00000000-0000-0000-0000-000000000001",
                "timestamp": "2026-06-19T00:00:00Z",
                "sequence_no": 1,
                "checksum": "abc123",
            },
        )

    db = _client(monkeypatch, handler, host="http://localhost:8000")
    try:
        asyncio.run(db.log(agent="test-agent", event="tool_call", data={"tool": "search"}))
    finally:
        asyncio.run(_close(db))

    err = capsys.readouterr().err
    assert "zizkadb why 00000000-0000-0000-0000-000000000001" in err


def test_log_hint_suppressed_when_quiet(monkeypatch, capsys):
    monkeypatch.setenv("ZIZKADB_QUIET", "1")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "event_id": "00000000-0000-0000-0000-000000000001",
                "timestamp": "2026-06-19T00:00:00Z",
                "sequence_no": 1,
                "checksum": "abc123",
            },
        )

    db = _client(monkeypatch, handler, host="http://localhost:8000")
    try:
        asyncio.run(db.log(agent="test-agent", event="tool_call", data={"tool": "search"}))
    finally:
        asyncio.run(_close(db))

    assert "zizkadb why" not in capsys.readouterr().err


@pytest.mark.parametrize(
    ("status", "payload", "error_type"),
    [
        (401, {"detail": "bad key"}, AuthError),
        (403, {"detail": "wrong agent"}, AgentScopeError),
        (404, {"detail": "missing"}, NotFoundError),
        (429, {"detail": "slow down"}, RateLimitError),
        (500, {"detail": "boom"}, ZizkaDBError),
    ],
)
def test_error_mapping(monkeypatch, status, payload, error_type):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    db = _client(monkeypatch, handler)
    try:
        with pytest.raises(error_type):
            asyncio.run(db.query(agent="test-agent"))
    finally:
        asyncio.run(_close(db))


def test_localhost_auto_injects_dev_key(monkeypatch):
    _prevent_telemetry(monkeypatch)
    monkeypatch.delenv("ZIZKADB_API_KEY", raising=False)
    monkeypatch.delenv("AGENTDB_API_KEY", raising=False)
    monkeypatch.delenv("DEV_API_KEY", raising=False)

    db = ZizkaDB(host="http://localhost:8000")

    assert db._api_key == "zizkadb_dev_local"
    assert db._headers()["Authorization"] == "Bearer zizkadb_dev_local"


def test_api_key_with_zizkadb_host_env_uses_that_host(monkeypatch):
    """A self-hosted key must not be sent to Cloud when ZIZKADB_HOST is set."""
    _prevent_telemetry(monkeypatch)
    monkeypatch.setenv("ZIZKADB_HOST", " https://zizka.example.com/ ")

    db = ZizkaDB(api_key="zizkadb_live_x")

    assert db._base_url == "https://zizka.example.com"
    assert db._api_key == "zizkadb_live_x"


def test_explicit_host_wins_over_zizkadb_host_env(monkeypatch):
    _prevent_telemetry(monkeypatch)
    monkeypatch.setenv("ZIZKADB_HOST", "https://env.example.com")

    db = ZizkaDB(api_key="zizkadb_live_x", host="https://arg.example.com")

    assert db._base_url == "https://arg.example.com"


def test_api_key_alone_means_cloud(monkeypatch):
    _prevent_telemetry(monkeypatch)
    monkeypatch.delenv("ZIZKADB_HOST", raising=False)

    db = ZizkaDB(api_key="zizkadb_live_x")

    assert db._base_url == "https://db.zizka.ai"


def test_zizkadb_host_env_alone_is_enough_for_localhost(monkeypatch):
    _prevent_telemetry(monkeypatch)
    for var in ("ZIZKADB_API_KEY", "AGENTDB_API_KEY", "DEV_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ZIZKADB_HOST", "http://localhost:8000")

    db = ZizkaDB()

    assert db._base_url == "http://localhost:8000"
    assert db._api_key == "zizkadb_dev_local"


def test_legacy_agentdb_host_env_is_honoured(monkeypatch):
    _prevent_telemetry(monkeypatch)
    monkeypatch.setenv("AGENTDB_HOST", "https://legacy.example.com")

    assert ZizkaDB(api_key="zizkadb_live_x")._base_url == "https://legacy.example.com"


def test_blank_zizkadb_host_falls_through_to_agentdb_host(monkeypatch):
    _prevent_telemetry(monkeypatch)
    monkeypatch.setenv("ZIZKADB_HOST", "  ")
    monkeypatch.setenv("AGENTDB_HOST", "https://legacy.example.com")

    assert ZizkaDB(api_key="k")._base_url == "https://legacy.example.com"


def test_explicit_host_is_trimmed_and_blank_means_unset(monkeypatch):
    _prevent_telemetry(monkeypatch)

    assert ZizkaDB(api_key="k", host=" https://x.example.com/ ")._base_url == "https://x.example.com"
    assert ZizkaDB(api_key="k", host="  ")._base_url == "https://db.zizka.ai"


def test_log_sends_null_for_omitted_parent_and_session(monkeypatch):
    """The SDK sends parent_id: null and session_id: null when not provided."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read().decode()
        return httpx.Response(200, json={"event_id": "evt-1", "timestamp": "2026-01-01T00:00:00Z", "sequence_no": 1, "checksum": "x"})

    db = _client(monkeypatch, handler)
    try:
        asyncio.run(db.log(agent="a", event="e", data={}))
    finally:
        asyncio.run(_close(db))

    body = json.loads(captured["body"])
    assert body["parent_id"] is None
    assert body["session_id"] is None


# ── Named tool / API helpers ─────────────────────────────────────────────────

def _capture_events(monkeypatch):
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.read().decode()))
        return httpx.Response(
            200,
            json={
                "event_id": f"00000000-0000-0000-0000-00000000000{len(bodies)}",
                "timestamp": "2026-06-19T00:00:00Z",
                "sequence_no": len(bodies),
                "checksum": "abc123",
            },
        )

    return _client(monkeypatch, handler), bodies


def test_log_tool_names_the_tool(monkeypatch):
    db, bodies = _capture_events(monkeypatch)
    try:
        asyncio.run(db.log_tool("bot", "lookup_order", args={"order_id": "ORD-1"}, session_id="s1"))
    finally:
        asyncio.run(_close(db))

    assert bodies[0]["event"] == "tool_call"
    assert bodies[0]["data"] == {"tool": "lookup_order", "args": {"order_id": "ORD-1"}}
    assert bodies[0]["session_id"] == "s1"


def test_log_tool_result_success_and_error(monkeypatch):
    db, bodies = _capture_events(monkeypatch)
    try:
        asyncio.run(db.log_tool_result("bot", "lookup_order", result={"ok": True}, duration_ms=12))
        asyncio.run(db.log_tool_result("bot", "lookup_order", error=TimeoutError("slow")))
    finally:
        asyncio.run(_close(db))

    assert bodies[0]["event"] == "tool_result"
    assert bodies[0]["data"] == {"tool": "lookup_order", "result": {"ok": True}, "duration_ms": 12}
    assert bodies[1]["event"] == "tool_error"
    assert bodies[1]["data"] == {"tool": "lookup_order", "error": "slow", "type": "TimeoutError"}


def test_log_api_names_the_call_and_drops_secrets_from_the_url(monkeypatch):
    db, bodies = _capture_events(monkeypatch)
    try:
        asyncio.run(
            db.log_api(
                "bot", "post",
                "https://user:pw@api.stripe.com:8443/v1/refunds?api_key=sk_live_secret#frag",
                status=502, duration_ms=310,
            )
        )
        asyncio.run(db.log_api("bot", "GET", "/internal/orders?token=abc", name="orders.list"))
    finally:
        asyncio.run(_close(db))

    assert bodies[0]["event"] == "api_call"
    assert bodies[0]["data"] == {
        "api": "POST api.stripe.com:8443/v1/refunds",
        "method": "POST",
        "url": "https://api.stripe.com:8443/v1/refunds",
        "status": 502,
        "duration_ms": 310,
    }
    assert "secret" not in json.dumps(bodies[0]) and "pw@" not in json.dumps(bodies[0])
    assert bodies[1]["data"] == {"api": "orders.list", "method": "GET", "url": "/internal/orders"}


def test_verify_chain_passes_anchor(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"agent": "a", "intact": True, "summary": {},
                                         "problems": [], "chain_head": None})

    db = _client(monkeypatch, handler)
    try:
        result = asyncio.run(db.verify_chain("a", after_seq=5, anchor="h5"))
    finally:
        asyncio.run(_close(db))
    assert result["intact"] is True
    assert "/v1/events/chain?agent=a&after_seq=5&anchor=h5" in seen["url"]

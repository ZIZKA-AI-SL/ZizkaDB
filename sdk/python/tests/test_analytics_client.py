import asyncio
import httpx
import pytest

from zizkadb.client import ZizkaDB


def _prevent_telemetry(monkeypatch):
    monkeypatch.setattr("zizkadb.client._telemetry_ping", lambda mode, sdk="python": None)
    monkeypatch.setenv("ZIZKADB_TELEMETRY", "false")


def _client(monkeypatch, handler):
    _prevent_telemetry(monkeypatch)
    db = ZizkaDB(api_key="zizkadb_live_test", host="https://example.test")
    db._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=db._base_url,
        headers=db._headers(),
    )
    return db


@pytest.mark.parametrize(
    ("method_name", "call", "expected_path"),
    [
        (
            "token_usage",
            lambda db: db.token_usage("a1", "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"),
            "/v1/agents/a1/token-usage",
        ),
        (
            "token_optimization",
            lambda db: db.token_optimization("a1"),
            "/v1/agents/a1/token-optimization",
        ),
        (
            "suggestions",
            lambda db: db.suggestions("a1"),
            "/v1/agents/a1/suggestions",
        ),
        (
            "report",
            lambda db: db.report("a1", "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"),
            "/v1/agents/a1/report",
        ),
        (
            "behavior_change",
            lambda db: db.behavior_change("a1"),
            "/v1/agents/a1/behavior-change",
        ),
    ],
)
def test_analytics_methods_hit_expected_paths(monkeypatch, method_name, call, expected_path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={"ok": True})

    db = _client(monkeypatch, handler)
    try:
        result = asyncio.run(call(db))
    finally:
        asyncio.run(db._client.aclose())

    assert seen["path"] == expected_path
    assert result == {"ok": True}


def test_analytics_methods_url_encode_agent_name(monkeypatch):
    """Agent names with special chars must be URL-encoded in the path.

    Asserts on raw_path (not .path) because httpx decodes .path and would
    hide missing encoding. Without the fix, '?' starts the query string.
    """
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["raw_path"] = request.url.raw_path
        return httpx.Response(200, json={"ok": True})

    db = _client(monkeypatch, handler)
    try:
        asyncio.run(db.token_usage("support bot?v2", "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"))
    finally:
        asyncio.run(db._client.aclose())

    raw_path = seen["raw_path"].decode()
    assert b"/v1/agents/support%20bot%3Fv2/token-usage" in raw_path, f"Expected encoded agent name, got: {raw_path}"
    assert b"support bot?v2" not in raw_path, f"Raw agent name should not appear in URL path: {raw_path}"

"""Tests for ZizkaDB.at() timestamp handling.

at() must accept datetime objects (as before) and ISO-8601 strings —
the form timestamps usually arrive in from JSON payloads, logs, and CLI
arguments — and reject anything else with a clear error.
"""

import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from zizkadb.client import ZizkaDB


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


def _at_response(request: httpx.Request, captures: dict) -> httpx.Response:
    captures["timestamp_param"] = request.url.params.get("timestamp")
    return httpx.Response(
        200,
        json={
            "agent": "test-agent",
            "at": "2026-05-01T15:00:00+00:00",
            "event_count": 3,
            "state": {"mood": "curious"},
        },
    )


def test_at_accepts_iso_string_with_z(monkeypatch):
    captures = {}

    def handler(request: httpx.Request) -> httpx.Response:
        return _at_response(request, captures)

    db = _client(monkeypatch, handler)
    try:
        state = asyncio.run(db.at("test-agent", "2030-01-01T00:00:00Z"))
    finally:
        asyncio.run(_close(db))
    assert captures["timestamp_param"] == "2030-01-01T00:00:00+00:00"
    assert state.agent == "test-agent"
    assert state.event_count == 3
    assert state.state == {"mood": "curious"}


def test_at_accepts_iso_string_with_offset(monkeypatch):
    captures = {}

    def handler(request: httpx.Request) -> httpx.Response:
        return _at_response(request, captures)

    db = _client(monkeypatch, handler)
    try:
        asyncio.run(db.at("test-agent", "2030-01-01T02:00:00+02:00"))
    finally:
        asyncio.run(_close(db))
    # The explicit offset is preserved; the server owns any conversion.
    assert captures["timestamp_param"] == "2030-01-01T02:00:00+02:00"


def test_at_accepts_aware_datetime(monkeypatch):
    captures = {}

    def handler(request: httpx.Request) -> httpx.Response:
        return _at_response(request, captures)

    db = _client(monkeypatch, handler)
    try:
        asyncio.run(db.at("test-agent", datetime(2026, 5, 1, 15, 0, tzinfo=timezone.utc)))
    finally:
        asyncio.run(_close(db))
    assert captures["timestamp_param"] == "2026-05-01T15:00:00+00:00"


def test_at_accepts_naive_datetime_unchanged(monkeypatch):
    """Naive datetimes are sent without an offset — the same convention as
    query()'s before/after parameters; the server interprets the wall time."""
    captures = {}

    def handler(request: httpx.Request) -> httpx.Response:
        return _at_response(request, captures)

    db = _client(monkeypatch, handler)
    try:
        asyncio.run(db.at("test-agent", datetime(2026, 5, 1, 15, 0)))
    finally:
        asyncio.run(_close(db))
    assert captures["timestamp_param"] == "2026-05-01T15:00:00"


def test_at_rejects_invalid_iso_string(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover - never reached
        raise AssertionError("request must not be sent for invalid input")

    db = _client(monkeypatch, handler)
    try:
        with pytest.raises(ValueError, match="Invalid ISO-8601 timestamp"):
            asyncio.run(db.at("test-agent", "not-a-date"))
    finally:
        asyncio.run(_close(db))


def test_at_rejects_non_timestamp_type(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover - never reached
        raise AssertionError("request must not be sent for invalid input")

    db = _client(monkeypatch, handler)
    try:
        with pytest.raises(TypeError, match="datetime or an ISO-8601 string"):
            asyncio.run(db.at("test-agent", 42))
    finally:
        asyncio.run(_close(db))

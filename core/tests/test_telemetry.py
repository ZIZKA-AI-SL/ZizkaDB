"""Tests for telemetry pings, country capture, and optional update subscriptions."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from api.telemetry import ping_limiter, updates_limiter
from main import app
from services.country_from_request import country_code_from_ip, country_code_from_request
from services.rate_limiter import InMemoryStorage

client = TestClient(app)

PING = {
    "install_id": "550e8400-e29b-41d4-a716-446655440000",
    "sdk": "python",
    "sdk_version": "0.2.5",
    "python": "3.12",
    "os": "Linux",
    "mode": "cloud",
}


class TestCountryFromRequest:
    def test_cf_ipcountry_header(self):
        request = MagicMock()
        request.headers = {"cf-ipcountry": "de"}
        request.client = MagicMock(host="203.0.113.1")
        assert country_code_from_request(request) == "DE"

    def test_private_ip_returns_none_without_geoip(self):
        assert country_code_from_ip("127.0.0.1") is None
        assert country_code_from_ip("192.168.1.4") is None


class TestTelemetryPing:
    def setup_method(self):
        ping_limiter.storage = InMemoryStorage()

    @patch("api.telemetry.get_pool")
    def test_flood_is_dropped_but_still_ok(self, mock_get_pool, monkeypatch):
        mock_pool = MagicMock()
        mock_pool.execute = AsyncMock()
        mock_get_pool.return_value = mock_pool
        monkeypatch.setattr(ping_limiter, "limit", 2)

        codes = [client.post("/v1/telemetry", json=PING).status_code for _ in range(4)]
        assert codes == [200] * 4
        assert mock_pool.execute.await_count == 2

    @patch("api.telemetry.get_pool")
    def test_ping_stores_country_from_header(self, mock_get_pool):
        mock_pool = MagicMock()
        mock_pool.execute = AsyncMock()
        mock_get_pool.return_value = mock_pool

        response = client.post(
            "/v1/telemetry",
            json=PING,
            headers={"CF-IPCountry": "US"},
        )
        assert response.status_code == 200
        assert response.json() == {"ok": True}

        args = mock_pool.execute.call_args[0]
        assert args[7] == "US"

    @patch("api.telemetry.get_pool")
    def test_ping_always_returns_ok_on_db_error(self, mock_get_pool):
        mock_pool = MagicMock()
        mock_pool.execute = AsyncMock(side_effect=RuntimeError("db down"))
        mock_get_pool.return_value = mock_pool

        response = client.post("/v1/telemetry", json=PING)
        assert response.status_code == 200
        assert response.json() == {"ok": True}

    @patch("api.telemetry.get_pool")
    def test_same_install_id_different_sdk_are_separate(self, mock_get_pool):
        mock_pool = MagicMock()
        mock_pool.execute = AsyncMock()
        mock_get_pool.return_value = mock_pool

        install_id = PING["install_id"]
        for sdk in ("python", "langchain", "crewai", "mcp", "typescript", "docker"):
            response = client.post(
                "/v1/telemetry",
                json={**PING, "sdk": sdk},
            )
            assert response.status_code == 200

        assert mock_pool.execute.call_count == 6
        sdks = {call.args[2] for call in mock_pool.execute.call_args_list}
        assert sdks == {"python", "langchain", "crewai", "mcp", "typescript", "docker"}


class TestTelemetryUpdates:
    def setup_method(self):
        updates_limiter.storage = InMemoryStorage()
        asyncio.run(updates_limiter.storage.clear())

    @patch("api.telemetry.get_pool")
    @patch("api.telemetry.is_suppressed", new_callable=AsyncMock, return_value=False)
    def test_subscribe_updates(self, _mock_suppressed, mock_get_pool):
        mock_pool = MagicMock()
        mock_pool.execute = AsyncMock()
        mock_get_pool.return_value = mock_pool

        response = client.post(
            "/v1/telemetry/updates",
            json={
                "email": "dev@example.com",
                "install_id": PING["install_id"],
                "sdk": "python",
                "source": "dashboard",
                "botcheck": "",
            },
            headers={"CF-IPCountry": "DE"},
        )
        assert response.status_code == 201
        assert response.json() == {"ok": True}

        args = mock_pool.execute.call_args[0]
        assert args[1] == "dev@example.com"
        assert args[2] == PING["install_id"]
        assert args[4] == "DE"

    def test_honeypot_rejected(self):
        response = client.post(
            "/v1/telemetry/updates",
            json={
                "email": "dev@example.com",
                "botcheck": "spam",
            },
        )
        assert response.status_code == 400

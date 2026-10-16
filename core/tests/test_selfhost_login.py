"""Self-hosted dashboard login: GET /v1/auth/selfhost + POST /v1/auth/selfhost-login."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from main import app
from api.auth import selfhost_login_limiter
from services.rate_limiter import InMemoryStorage

client = TestClient(app)


@pytest.fixture(autouse=True)
def _fresh_limiter():
    selfhost_login_limiter.storage = InMemoryStorage()
    asyncio.run(selfhost_login_limiter.storage.clear())


@pytest.fixture
def mock_pool():
    with patch("api.auth.get_pool") as get_pool, patch(
        "api.auth._ensure_dev_tenant", new_callable=AsyncMock
    ) as ensure:
        yield get_pool, ensure


def _env(monkeypatch, *, mode=None, env=None, token=None):
    for key, value in (("DEPLOYMENT_MODE", mode), ("ENV", env), ("SELFHOST_ADMIN_TOKEN", token)):
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


class TestSelfHostConfig:
    def test_managed_production_reports_not_self_hosted(self, monkeypatch):
        _env(monkeypatch, mode="managed", env="production", token="s3cret")
        assert client.get("/v1/auth/selfhost").json() == {"self_hosted": False, "login": None}

    def test_deployment_mode_unset_in_production_is_managed(self, monkeypatch):
        _env(monkeypatch, env="production")
        assert client.get("/v1/auth/selfhost").json()["self_hosted"] is False

    def test_dev_without_deployment_mode_is_one_click(self, monkeypatch):
        # Older local .env files predate DEPLOYMENT_MODE — same rule as /dev-token.
        _env(monkeypatch, env="development")
        assert client.get("/v1/auth/selfhost").json() == {"self_hosted": True, "login": "one_click"}

    def test_dev_is_one_click(self, monkeypatch):
        _env(monkeypatch, mode="self_hosted", env="development")
        assert client.get("/v1/auth/selfhost").json() == {"self_hosted": True, "login": "one_click"}

    def test_env_unset_is_production_not_one_click(self, monkeypatch):
        """Unset ENV fails closed: no one-click sign-in without ENV=development."""
        _env(monkeypatch, mode="self_hosted")
        assert client.get("/v1/auth/selfhost").json()["login"] == "unavailable"

    def test_env_unset_with_token_is_admin_token(self, monkeypatch):
        _env(monkeypatch, mode="self_hosted", token="s3cret")
        assert client.get("/v1/auth/selfhost").json()["login"] == "admin_token"

    def test_production_with_token_is_admin_token(self, monkeypatch):
        _env(monkeypatch, mode="self_hosted", env="production", token="s3cret")
        assert client.get("/v1/auth/selfhost").json()["login"] == "admin_token"

    def test_production_without_token_is_unavailable(self, monkeypatch):
        _env(monkeypatch, mode="self_hosted", env="production", token="   ")
        assert client.get("/v1/auth/selfhost").json()["login"] == "unavailable"

    def test_config_never_leaks_token(self, monkeypatch):
        _env(monkeypatch, mode="self_hosted", env="production", token="s3cret")
        assert "s3cret" not in client.get("/v1/auth/selfhost").text


class TestSelfHostLogin:
    def test_managed_production_returns_404(self, monkeypatch, mock_pool):
        _env(monkeypatch, mode="managed", env="production", token="s3cret")
        assert client.post("/v1/auth/selfhost-login", json={"token": "s3cret"}).status_code == 404
        mock_pool[1].assert_not_awaited()

    def test_one_click_issues_token_without_body(self, monkeypatch, mock_pool):
        _env(monkeypatch, mode="self_hosted", env="development")
        res = client.post("/v1/auth/selfhost-login")
        assert res.status_code == 200
        assert res.json()["access_token"]
        mock_pool[1].assert_awaited_once()

    def test_admin_token_required_in_production(self, monkeypatch, mock_pool):
        _env(monkeypatch, mode="self_hosted", env="production", token="s3cret")
        assert client.post("/v1/auth/selfhost-login").status_code == 401
        assert client.post("/v1/auth/selfhost-login", json={"token": "wrong"}).status_code == 401
        mock_pool[1].assert_not_awaited()

    def test_correct_admin_token_logs_in(self, monkeypatch, mock_pool):
        _env(monkeypatch, mode="self_hosted", env="production", token="s3cret")
        res = client.post("/v1/auth/selfhost-login", json={"token": "  s3cret "})
        assert res.status_code == 200
        assert res.json()["access_token"]

    def test_production_without_token_is_forbidden(self, monkeypatch, mock_pool):
        _env(monkeypatch, mode="self_hosted", env="production")
        assert client.post("/v1/auth/selfhost-login", json={"token": ""}).status_code == 403
        mock_pool[1].assert_not_awaited()

    def test_rate_limited(self, monkeypatch, mock_pool):
        _env(monkeypatch, mode="self_hosted", env="production", token="s3cret")
        codes = [
            client.post("/v1/auth/selfhost-login", json={"token": "wrong"}).status_code
            for _ in range(11)
        ]
        assert codes[:10] == [401] * 10
        assert codes[10] == 429

"""Unset ENV means production: development mode is opt-in (services/runtime_env.py)."""

import pytest
from fastapi.testclient import TestClient

from main import app, lifespan
from services.auth import _required_secret
from services.runtime_env import env_name, is_dev_mode

client = TestClient(app)


@pytest.mark.parametrize(
    "env,dev",
    [
        (None, False),
        ("", False),
        ("production", False),
        ("staging", False),
        ("dev", False),
        ("developmnet", False),
        ("development", True),
        (" Development ", True),
    ],
)
def test_only_explicit_development_is_dev_mode(monkeypatch, env, dev):
    if env is None:
        monkeypatch.delenv("ENV", raising=False)
    else:
        monkeypatch.setenv("ENV", env)
    assert is_dev_mode() is dev


def test_env_name_is_normalised(monkeypatch):
    monkeypatch.setenv("ENV", "  Production ")
    assert env_name() == "production"


def test_dev_token_refused_when_env_unset(monkeypatch):
    monkeypatch.delenv("ENV", raising=False)
    response = client.post("/v1/auth/dev-token")
    assert response.status_code == 403


def test_missing_jwt_secret_refused_when_env_unset(monkeypatch):
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("ZIZKADB_TEST_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="ENV=development"):
        _required_secret("ZIZKADB_TEST_SECRET", "dev-secret")


@pytest.mark.parametrize("env", [None, "staging"])
async def test_startup_refuses_published_jwt_secret_outside_development(monkeypatch, env):
    """The real lifespan runs the guard before touching any database."""

    async def _init_db_must_not_run():
        raise AssertionError("startup guard did not run before init_db()")

    monkeypatch.setattr("main.init_db", _init_db_must_not_run)
    if env is None:
        monkeypatch.delenv("ENV", raising=False)
    else:
        monkeypatch.setenv("ENV", env)
    monkeypatch.delenv("DEV_API_KEY", raising=False)
    monkeypatch.setenv("JWT_SECRET", "dev-secret-change-in-production")
    monkeypatch.setenv("JWT_REFRESH_SECRET", "a-unique-refresh-secret-for-this-test")

    with pytest.raises(RuntimeError, match="JWT"):
        async with lifespan(app):
            pass

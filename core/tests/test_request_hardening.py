"""Community link host sanitising, media serving, REDIS_PASSWORD and QDRANT_API_KEY."""

from types import SimpleNamespace

import pytest

from api.community import _public_base_url
from db.connection import qdrant_api_key, redis_url


def _req(peer, xff=None, host=None, proto=None):
    headers = {}
    if xff is not None:
        headers["x-forwarded-for"] = xff
    if host is not None:
        headers["host"] = host
    if proto is not None:
        headers["x-forwarded-proto"] = proto
    return SimpleNamespace(
        client=SimpleNamespace(host=peer),
        headers=headers,
        url=SimpleNamespace(scheme="http"),
    )


class TestPublicBaseUrl:
    @pytest.fixture(autouse=True)
    def _no_env(self, monkeypatch):
        monkeypatch.delenv("PUBLIC_API_URL", raising=False)

    def test_normal_host_kept(self):
        assert _public_base_url(_req("1.1.1.1", host="api.example.com:8443")) == "http://api.example.com:8443"

    @pytest.mark.parametrize("host", ["evil.com/x?", "a b", "evil.com@x", ""])
    def test_bad_host_replaced(self, host):
        assert _public_base_url(_req("1.1.1.1", host=host)) == "http://db.zizka.ai"

    def test_bad_scheme_replaced(self):
        assert _public_base_url(_req("1.1.1.1", host="a.com", proto="javascript")) == "https://a.com"

    def test_env_wins(self, monkeypatch):
        monkeypatch.setenv("PUBLIC_API_URL", "https://api.mine.dev/")
        assert _public_base_url(_req("1.1.1.1", host="evil.com")) == "https://api.mine.dev"


class TestRedisAndQdrantCredentials:
    def test_no_password_keeps_url(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://redis:6379")
        monkeypatch.delenv("REDIS_PASSWORD", raising=False)
        assert redis_url() == "redis://redis:6379"

    def test_password_added(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
        monkeypatch.setenv("REDIS_PASSWORD", "p@ss:word")
        assert redis_url() == "redis://:p%40ss%3Aword@redis:6379/0"

    def test_url_credentials_win(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://:inurl@redis:6379")
        monkeypatch.setenv("REDIS_PASSWORD", "other")
        assert redis_url() == "redis://:inurl@redis:6379"

    def test_qdrant_key_empty_is_none(self, monkeypatch):
        monkeypatch.setenv("QDRANT_API_KEY", "  ")
        assert qdrant_api_key() is None
        monkeypatch.setenv("QDRANT_API_KEY", "k")
        assert qdrant_api_key() == "k"


class TestServeMedia:
    @pytest.fixture
    def media_client(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        import api.community as community
        from main import app

        monkeypatch.setattr(community, "UPLOAD_DIR", tmp_path / "uploads")
        (tmp_path / "uploads").mkdir()
        (tmp_path / "uploads" / "0123456789abcdef0123456789abcdef.png").write_bytes(b"\x89PNG")
        (tmp_path / "secret.png").write_bytes(b"secret")
        return TestClient(app)

    def test_serves_upload_with_nosniff(self, media_client):
        r = media_client.get("/v1/community/media/0123456789abcdef0123456789abcdef.png")
        assert r.status_code == 200
        assert r.headers["x-content-type-options"] == "nosniff"

    @pytest.mark.parametrize("name", ["..", "missing.png", "..%2Fsecret.png", "...png"])
    def test_outside_or_missing_is_404(self, media_client, name):
        assert media_client.get(f"/v1/community/media/{name}").status_code == 404


class TestDefaultDbPasswordWarning:
    @pytest.mark.parametrize("env,url,warns", [
        ("production", "postgresql://zizkadb:zizkadb@postgres:5432/zizkadb", True),
        ("production", "postgresql://zizkadb:3f9a1c@postgres:5432/zizkadb", False),
        ("production", "", False),
        ("development", "postgresql://zizkadb:zizkadb@localhost:5432/zizkadb", False),
    ])
    def test_warns_only_in_production_with_default(self, monkeypatch, env, url, warns):
        from db.connection import uses_default_db_password

        monkeypatch.setenv("ENV", env)
        monkeypatch.setenv("DATABASE_URL", url)
        assert uses_default_db_password() is warns

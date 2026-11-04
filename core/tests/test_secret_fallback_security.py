"""
Unit tests for the insecure-fallback-secret fix in auth.py and
embedding_config.py.

These tests verify that:
1. In development (ENV unset or "development"), the hardcoded fallback
   secrets still work, so local self-host / first-run experience is
   unaffected.
2. In production (ENV set to anything else), missing secrets raise
   RuntimeError instead of silently using a value that is visible in
   the public source code.

No database or network required — these are pure function tests against
the secret-resolution logic.
"""

import importlib
import os
import sys
import types
from unittest.mock import MagicMock

import pytest

# embedding_config.py imports `from db.connection import get_pool`, which in
# turn imports asyncpg + redis. These tests only exercise pure secret-
# resolution logic and never touch the database, so we stub db.connection
# out rather than requiring the full infra dependency chain to be installed.
# Install the stub per test and restore it afterwards: replacing it for the
# whole session would hand a MagicMock pool to any later code that imports
# db.connection lazily (e.g. API-key binding in the real-Postgres tests).
@pytest.fixture(autouse=True)
def _stub_db_connection(monkeypatch):
    fake = types.ModuleType("db.connection")
    fake.get_pool = MagicMock()
    if "db" not in sys.modules:
        monkeypatch.setitem(sys.modules, "db", types.ModuleType("db"))
    monkeypatch.setitem(sys.modules, "db.connection", fake)
    # Modules imported under the stub must not outlive it either.
    name = "services.embedding_config"
    loaded = name in sys.modules
    if loaded:
        monkeypatch.setitem(sys.modules, name, sys.modules[name])
    yield
    if not loaded:
        sys.modules.pop(name, None)


def _reload_with_env(module_name: str, env: dict) -> None:
    """Reload a module with a patched environment so its module-level
    secret resolution re-runs under the new env vars."""
    old_env = {k: os.environ.get(k) for k in env}
    try:
        for k, v in env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if module_name in sys.modules:
            del sys.modules[module_name]
        return importlib.import_module(module_name)
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ── embedding_config.py — _fernet() ─────────────────────────────────────────

class TestEmbeddingKeyFallback:
    def test_dev_fallback_when_no_secrets_set(self, monkeypatch):
        monkeypatch.delenv("EMBEDDING_ENCRYPTION_KEY", raising=False)
        monkeypatch.delenv("JWT_SECRET", raising=False)
        monkeypatch.setenv("ENV", "development")
        from services.embedding_config import _fernet
        f = _fernet()
        # Should produce a working Fernet instance, not raise
        token = f.encrypt(b"test")
        assert f.decrypt(token) == b"test"

    def test_uses_embedding_encryption_key_when_set(self, monkeypatch):
        monkeypatch.setenv("EMBEDDING_ENCRYPTION_KEY", "my-encryption-key")
        monkeypatch.setenv("ENV", "production")
        from services.embedding_config import _fernet
        f = _fernet()
        token = f.encrypt(b"secret-api-key")
        assert f.decrypt(token) == b"secret-api-key"

    def test_falls_back_to_jwt_secret_when_embedding_key_unset(self, monkeypatch):
        monkeypatch.delenv("EMBEDDING_ENCRYPTION_KEY", raising=False)
        monkeypatch.setenv("JWT_SECRET", "shared-jwt-secret")
        monkeypatch.setenv("ENV", "production")
        from services.embedding_config import _fernet
        f = _fernet()
        token = f.encrypt(b"test")
        assert f.decrypt(token) == b"test"

    def test_production_without_any_secret_raises(self, monkeypatch):
        monkeypatch.delenv("EMBEDDING_ENCRYPTION_KEY", raising=False)
        monkeypatch.delenv("JWT_SECRET", raising=False)
        monkeypatch.setenv("ENV", "production")
        from services.embedding_config import _fernet
        with pytest.raises(RuntimeError, match="must be set in production"):
            _fernet()

    def test_round_trip_encrypt_decrypt_api_key(self, monkeypatch):
        monkeypatch.setenv("EMBEDDING_ENCRYPTION_KEY", "test-key-for-roundtrip")
        from services.embedding_config import encrypt_api_key, decrypt_api_key
        original = "sk-test-1234567890abcdef"
        encrypted = encrypt_api_key(original)
        assert encrypted != original
        assert decrypt_api_key(encrypted) == original

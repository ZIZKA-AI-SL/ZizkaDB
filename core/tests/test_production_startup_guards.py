"""Production startup guards — refuse insecure defaults when ENV=production."""

import pytest

from api.deps import _dev_key_accepted
from main import validate_production_startup


class TestValidateProductionStartup:
    def test_development_allows_default_secrets(self):
        validate_production_startup("development", "zizkadb_dev_local", "")

    def test_production_rejects_default_dev_key(self):
        with pytest.raises(RuntimeError, match="DEV_API_KEY"):
            validate_production_startup(
                "production",
                "zizkadb_dev_local",
                "strong-jwt-secret",
            )

    def test_production_rejects_legacy_default_dev_key(self):
        with pytest.raises(RuntimeError, match="DEV_API_KEY"):
            validate_production_startup(
                "production",
                "agdb_dev_local",
                "strong-jwt-secret",
            )

    def test_production_rejects_empty_dev_key(self):
        with pytest.raises(RuntimeError, match="DEV_API_KEY"):
            validate_production_startup("production", "", "strong-jwt-secret")

    def test_production_rejects_default_jwt_secret(self):
        with pytest.raises(RuntimeError, match="JWT_SECRET"):
            validate_production_startup(
                "production",
                "unique-dev-key",
                "dev-secret-change-in-production",
            )

    def test_production_rejects_empty_jwt_secret(self):
        with pytest.raises(RuntimeError, match="JWT_SECRET"):
            validate_production_startup("production", "unique-dev-key", "")

    def test_production_accepts_unique_secrets(self):
        validate_production_startup(
            "production",
            "unique-dev-key",
            "unique-jwt-secret",
        )


class TestDevKeyRejectedInProduction:
    def test_known_dev_keys_not_accepted(self, monkeypatch):
        monkeypatch.setattr("api.deps._DEV_MODE", False)
        assert not _dev_key_accepted("zizkadb_dev_local")
        assert not _dev_key_accepted("agdb_dev_local")

    def test_custom_dev_key_not_accepted_in_production(self, monkeypatch):
        monkeypatch.setattr("api.deps._DEV_MODE", False)
        monkeypatch.setattr("api.deps._DEV_API_KEY", "my-custom-dev-key")
        assert not _dev_key_accepted("my-custom-dev-key")

    def test_dev_keys_accepted_in_development(self, monkeypatch):
        monkeypatch.setattr("api.deps._DEV_MODE", True)
        monkeypatch.setattr("api.deps._DEV_API_KEY", "")
        assert _dev_key_accepted("zizkadb_dev_local")

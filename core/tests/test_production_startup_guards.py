"""Production startup guards — refuse insecure defaults when ENV=production."""

import pytest

from api.deps import _dev_key_accepted
from main import validate_production_startup

_UNIQUE_ACCESS = "unique-jwt-secret"
_UNIQUE_REFRESH = "unique-refresh-secret"
_UNIQUE_DEV_KEY = "unique-dev-key"


class TestValidateProductionStartup:
    def test_development_allows_default_secrets(self):
        validate_production_startup("development", "zizkadb_dev_local", "")

    def test_production_rejects_default_dev_key(self):
        with pytest.raises(RuntimeError, match="DEV_API_KEY"):
            validate_production_startup(
                "production",
                "zizkadb_dev_local",
                _UNIQUE_ACCESS,
                jwt_refresh_secret=_UNIQUE_REFRESH,
            )

    def test_production_rejects_legacy_default_dev_key(self):
        with pytest.raises(RuntimeError, match="DEV_API_KEY"):
            validate_production_startup(
                "production",
                "agdb_dev_local",
                _UNIQUE_ACCESS,
                jwt_refresh_secret=_UNIQUE_REFRESH,
            )

    def test_production_rejects_empty_dev_key(self):
        with pytest.raises(RuntimeError, match="DEV_API_KEY"):
            validate_production_startup(
                "production",
                "",
                _UNIQUE_ACCESS,
                jwt_refresh_secret=_UNIQUE_REFRESH,
            )

    @pytest.mark.parametrize(
        "jwt_secret,jwt_refresh_secret",
        [
            ("dev-secret-change-in-production", _UNIQUE_REFRESH),
            ("change-this-to-a-random-32-char-string", _UNIQUE_REFRESH),
            ("dev-secret", _UNIQUE_REFRESH),
            (_UNIQUE_ACCESS, "dev-refresh-secret-change-in-production"),
            (_UNIQUE_ACCESS, "change-this-to-another-random-32-char-string"),
            (_UNIQUE_ACCESS, "dev-refresh-secret"),
            ("", _UNIQUE_REFRESH),
            (_UNIQUE_ACCESS, ""),
        ],
    )
    def test_production_rejects_published_jwt_placeholders(
        self, jwt_secret, jwt_refresh_secret
    ):
        with pytest.raises(RuntimeError, match="JWT"):
            validate_production_startup(
                "production",
                _UNIQUE_DEV_KEY,
                jwt_secret,
                jwt_refresh_secret=jwt_refresh_secret,
            )

    def test_production_rejects_env_production_with_wrong_casing(self):
        with pytest.raises(RuntimeError, match="JWT"):
            validate_production_startup(
                "Production",
                _UNIQUE_DEV_KEY,
                "dev-secret-change-in-production",
                jwt_refresh_secret=_UNIQUE_REFRESH,
            )

    def test_production_rejects_env_with_surrounding_whitespace(self):
        with pytest.raises(RuntimeError, match="JWT"):
            validate_production_startup(
                " production ",
                _UNIQUE_DEV_KEY,
                "dev-secret-change-in-production",
                jwt_refresh_secret=_UNIQUE_REFRESH,
            )

    def test_production_rejects_short_selfhost_admin_token(self):
        with pytest.raises(RuntimeError, match="SELFHOST_ADMIN_TOKEN"):
            validate_production_startup(
                "production",
                _UNIQUE_DEV_KEY,
                _UNIQUE_ACCESS,
                "admin",
                jwt_refresh_secret=_UNIQUE_REFRESH,
            )

    def test_production_accepts_strong_or_unset_selfhost_admin_token(self):
        validate_production_startup(
            "production",
            _UNIQUE_DEV_KEY,
            _UNIQUE_ACCESS,
            "x" * 16,
            jwt_refresh_secret=_UNIQUE_REFRESH,
        )
        validate_production_startup(
            "production",
            _UNIQUE_DEV_KEY,
            _UNIQUE_ACCESS,
            "",
            jwt_refresh_secret=_UNIQUE_REFRESH,
        )

    def test_development_ignores_short_selfhost_admin_token(self):
        validate_production_startup("development", "zizkadb_dev_local", "", "admin")

    def test_production_accepts_unique_secrets(self):
        validate_production_startup(
            "production",
            _UNIQUE_DEV_KEY,
            _UNIQUE_ACCESS,
            jwt_refresh_secret=_UNIQUE_REFRESH,
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

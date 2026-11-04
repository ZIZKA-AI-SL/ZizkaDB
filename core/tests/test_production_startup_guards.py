"""Production startup guards — refuse insecure defaults when ENV=production."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from api.deps import _dev_key_accepted
from main import _DEFAULT_JWT_SECRETS, validate_production_startup

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

    def test_production_allows_empty_dev_key(self):
        """Dev keys are never accepted outside development, so none is needed."""
        validate_production_startup(
            "production",
            "",
            _UNIQUE_ACCESS,
            jwt_refresh_secret=_UNIQUE_REFRESH,
        )

    @pytest.mark.parametrize("env", ["", "staging", "prod", "Production", " test "])
    def test_every_non_development_env_is_checked(self, env):
        with pytest.raises(RuntimeError, match="JWT"):
            validate_production_startup(
                env,
                _UNIQUE_DEV_KEY,
                "dev-secret-change-in-production",
                jwt_refresh_secret=_UNIQUE_REFRESH,
            )

    @pytest.mark.parametrize("env", ["development", " Development "])
    def test_development_is_not_checked(self, env):
        validate_production_startup(env, "zizkadb_dev_local", "", jwt_refresh_secret="")

    @pytest.mark.parametrize(
        "jwt_secret,jwt_refresh_secret",
        [
            ("dev-secret-change-in-production", _UNIQUE_REFRESH),
            ("change-this-to-a-random-32-char-string", _UNIQUE_REFRESH),
            ("dev-secret", _UNIQUE_REFRESH),
            (_UNIQUE_ACCESS, "dev-refresh-secret-change-in-production"),
            (_UNIQUE_ACCESS, "change-this-to-another-random-32-char-string"),
            (_UNIQUE_ACCESS, "dev-refresh-secret"),
            ("your-random-32-char-secret", _UNIQUE_REFRESH),
            (_UNIQUE_ACCESS, "your-random-32-char-secret"),
            (" dev-secret ", _UNIQUE_REFRESH),
            (_UNIQUE_ACCESS, "your-random-32-char-secret\r"),
            ("   ", _UNIQUE_REFRESH),
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
        monkeypatch.setenv("ENV", "production")
        assert not _dev_key_accepted("zizkadb_dev_local")
        assert not _dev_key_accepted("agdb_dev_local")

    def test_custom_dev_key_not_accepted_in_production(self, monkeypatch):
        monkeypatch.setenv("ENV", "production")
        monkeypatch.setattr("api.deps._DEV_API_KEY", "my-custom-dev-key")
        assert not _dev_key_accepted("my-custom-dev-key")

    def test_dev_keys_accepted_in_development(self, monkeypatch):
        monkeypatch.setenv("ENV", "development")
        monkeypatch.setattr("api.deps._DEV_API_KEY", "")
        assert _dev_key_accepted("zizkadb_dev_local")


_REPO = Path(__file__).resolve().parents[2]
_PREFLIGHT = _REPO / "scripts" / "validate-selfhost-config.sh"

# Text files that can show a JWT secret value: templates, docs, Compose files,
# scripts and code. Test files are left out; they use made-up values on purpose.
_SCANNED_SUFFIXES = frozenset(
    {".example", ".quickstart", ".md", ".mdc", ".mdx", ".txt", ".yml", ".yaml",
     ".toml", ".json", ".sh", ".py", ".js", ".mjs", ".ts", ".tsx"}
)
_SECRET_VALUE_PATTERNS = (
    re.compile(r"\bJWT_(?:REFRESH_)?SECRET=[\"']?([^\s\"'`#]*)"),  # JWT_SECRET=value
    re.compile(r"\$\{JWT_(?:REFRESH_)?SECRET:-([^}]*)\}"),  # ${JWT_SECRET:-value}
    re.compile(r"(?m)^[ \t-]*JWT_(?:REFRESH_)?SECRET:[ \t]*[\"']?([^\s\"'#]*)"),  # YAML
    re.compile(r'_required_secret\(\s*"JWT_(?:REFRESH_)?SECRET",\s*"([^"]*)"'),  # auth.py
)


def _tracked_files() -> list[Path] | None:
    """Files committed to git, so local env files with real secrets are never read."""
    if shutil.which("git") is None:
        return None
    result = subprocess.run(
        ["git", "-C", str(_REPO), "ls-files", "-z"], capture_output=True, check=False
    )
    if result.returncode != 0:
        return None
    return [Path(name) for name in result.stdout.decode().split("\0") if name]


def _is_scanned(rel: Path) -> bool:
    if rel.suffix not in _SCANNED_SUFFIXES and rel.name != "Dockerfile":
        return False
    if {"tests", "__tests__"} & set(rel.parts):
        return False
    return ".test." not in rel.name and ".spec." not in rel.name


def _published_jwt_secrets(files: list[Path]) -> dict[str, list[str]]:
    """Every literal JWT secret value in the given files, with file:line locations."""
    found: dict[str, list[str]] = {}
    for rel in filter(_is_scanned, files):
        path = _REPO / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for regex in _SECRET_VALUE_PATTERNS:
            for match in regex.finditer(text):
                value = match.group(1)
                if value.startswith("$"):  # $(openssl ...), ${GENERATED}
                    continue
                line = text.count("\n", 0, match.start()) + 1
                found.setdefault(value, []).append(f"{rel}:{line}")
    return found


@pytest.mark.skipif(
    not _PREFLIGHT.is_file(), reason="needs the full repository, not only core/"
)
class TestPublishedJwtSecretsAreRefused:
    def test_every_published_sample_is_refused(self):
        files = _tracked_files()
        if files is None:
            pytest.skip("needs a git checkout to list committed files")
        found = _published_jwt_secrets(files)
        assert "dev-secret" in found, "scanner found nothing; check _SCANNED_SUFFIXES"
        # Locations only: a file can hold a local edit with a real secret.
        missing = sorted(
            loc for value, locs in found.items() if value not in _DEFAULT_JWT_SECRETS
            for loc in locs
        )
        assert not missing, (
            "JWT secret values that production would accept; add them to "
            f"core/main.py _DEFAULT_JWT_SECRETS and the preflight script: {missing}"
        )

    def test_preflight_placeholders_match_api(self):
        script = _PREFLIGHT.read_text(encoding="utf-8")
        block = re.search(r"_JWT_PLACEHOLDERS=\((.*?)\n\)", script, re.S)
        assert block, "_JWT_PLACEHOLDERS array not found in validate-selfhost-config.sh"
        preflight = set(re.findall(r'"([^"]*)"', block.group(1)))
        assert preflight == _DEFAULT_JWT_SECRETS - {""}


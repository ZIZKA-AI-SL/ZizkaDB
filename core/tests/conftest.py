import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT.parent / "sdk" / "python"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SDK))

# Unset ENV means production (services/runtime_env.py), which refuses to import
# without real secrets. The suite runs as a local dev stack does; tests that
# need production behaviour set ENV themselves.
os.environ.setdefault("ENV", "development")


def _truthy(value: str | None) -> bool:
    return (value or "").lower() in {"1", "true", "yes", "on"}


def pytest_addoption(parser):
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run integration tests that require a running ZizkaDB stack.",
    )


def pytest_collection_modifyitems(config, items):
    if not os.getenv("ZIZKADB_TEST_DATABASE_URL"):
        skip_postgres = pytest.mark.skip(
            reason=(
                "requires a real Postgres with pgvector; set "
                "ZIZKADB_TEST_DATABASE_URL to enable"
            )
        )
        for item in items:
            if "postgres" in item.keywords:
                item.add_marker(skip_postgres)

    run_integration = config.getoption("--run-integration") or _truthy(
        os.getenv("ZIZKADB_RUN_INTEGRATION")
    )
    if run_integration:
        return

    skip_integration = pytest.mark.skip(
        reason=(
            "requires a running ZizkaDB stack; set ZIZKADB_RUN_INTEGRATION=1 "
            "or pass --run-integration to enable"
        )
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)


# --------------------------------------------------------------------
# Real-Postgres fixtures (tests marked ``postgres``)
# --------------------------------------------------------------------

class _StubQdrant:
    """Stands in for AsyncQdrantClient so init_db() needs no Qdrant server."""

    def __init__(self, *args, **kwargs):
        self._collections: list[str] = []

    async def get_collections(self):
        from types import SimpleNamespace

        return SimpleNamespace(
            collections=[SimpleNamespace(name=n) for n in self._collections]
        )

    async def create_collection(self, collection_name, **kwargs):
        self._collections.append(collection_name)

    async def close(self):
        return None


def _dsn_for_database(dsn: str, database: str) -> str:
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(dsn)
    return urlunsplit(parts._replace(path=f"/{database}"))


@pytest.fixture
async def pg_db(monkeypatch):
    """A throwaway database on the test server, set up like a fresh install.

    Applies ``db/schema.sql`` (what Docker runs on first boot), then
    ``init_db()`` (what every API start runs). Yields the asyncpg pool that
    ``get_pool()`` returns; the database is dropped afterwards.

    Only the uniquely named database it creates is ever written to or dropped;
    the database named in ``ZIZKADB_TEST_DATABASE_URL`` is used to issue
    CREATE/DROP DATABASE and is otherwise left alone.
    """
    import uuid

    import asyncpg
    import db.connection as connection

    async def _no_qdrant_version() -> None:
        return None

    admin_dsn = os.environ["ZIZKADB_TEST_DATABASE_URL"]
    database = f"zizkadb_test_{uuid.uuid4().hex[:12]}"
    dsn = _dsn_for_database(admin_dsn, database)

    admin = await asyncpg.connect(admin_dsn)
    try:
        await admin.execute(f'CREATE DATABASE "{database}"')

        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute((ROOT / "db" / "schema.sql").read_text())
        finally:
            await conn.close()

        monkeypatch.setenv("DATABASE_URL", dsn)
        monkeypatch.setenv("DEPLOYMENT_MODE", "self_hosted")
        monkeypatch.setenv("EMBEDDINGS_ENABLED", "false")
        monkeypatch.setattr(connection, "AsyncQdrantClient", _StubQdrant)
        monkeypatch.setattr(connection, "_log_qdrant_version", _no_qdrant_version)
        # Restored to their previous values when the test ends.
        monkeypatch.setattr(connection, "_pg_pool", None)
        monkeypatch.setattr(connection, "_redis", None)
        monkeypatch.setattr(connection, "_qdrant", None)

        try:
            await connection.init_db()
            yield connection.get_pool()
        finally:
            # Also closes a pool left open by an init_db() that failed midway.
            await connection.close_db()
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        await admin.close()

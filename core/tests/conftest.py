import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT.parent / "sdk" / "python"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SDK))


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


class _AsyncCM:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *exc):
        return False


def use_pool_as_connection(pool):
    """Let a mocked pool serve ``async with pool.acquire() as conn`` and
    ``async with conn.transaction()``, with the pool itself as the connection,
    so assertions on ``pool.fetchrow`` / ``pool.execute`` keep working."""
    from unittest.mock import MagicMock

    pool.acquire = MagicMock(return_value=_AsyncCM(pool))
    pool.transaction = MagicMock(return_value=_AsyncCM(None))
    return pool


def stub_audit_chain(monkeypatch, chain_hash="c" * 64):
    """Skip hash-chain SQL in unit tests that mock the pool."""
    from unittest.mock import AsyncMock

    monkeypatch.setattr("services.audit_chain.lock_tenant_chain", AsyncMock())
    monkeypatch.setattr(
        "services.audit_chain.append_event", AsyncMock(return_value=chain_hash)
    )

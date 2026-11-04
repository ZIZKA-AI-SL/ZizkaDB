"""`zizkadb init` templates and the examples pick the server the same way.

An explicit ZIZKADB_HOST wins (self-hosted with an API key), a key alone means
ZizkaDB Cloud, and neither means the local dev stack.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[3]
TEMPLATES = ROOT / "sdk" / "python" / "zizkadb" / "templates"
SCRIPTS = {
    **{f"template:{t}": TEMPLATES / t / "agent.py" for t in ("basic", "openai", "langchain", "crewai", "langgraph", "rag")},
    **{
        f"example:{e}": ROOT / "examples" / e / "agent.py"
        for e in ("minimal-python", "openai-agent", "anthropic-agent", "langchain-agent", "crewai-agent")
    },
}
# Third-party packages the templates import; stubbed so the test needs none.
STUBBED = [
    "openai",
    "anthropic",
    "crewai",
    "langchain_core",
    "langchain_core.messages",
    "langchain_openai",
    "zizkadb.integrations.langchain",
    "zizkadb.integrations.crewai",
    "zizkadb_langchain",
    "zizkadb_crewai",
    "zizkadb_langgraph",
]


class _Built(Exception):
    """Raised by the fake client: we only need the constructor arguments."""


def _client_kwargs(name: str, monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> dict:
    for var in ("ZIZKADB_API_KEY", "ZIZKADB_HOST"):
        monkeypatch.delenv(var, raising=False)
    for var, value in env.items():
        monkeypatch.setenv(var, value)
    for mod in STUBBED:
        monkeypatch.setitem(sys.modules, mod, MagicMock())

    seen: dict = {}

    def fake_client(**kwargs):
        seen.update(kwargs)
        raise _Built

    spec = importlib.util.spec_from_file_location("agent_under_test", SCRIPTS[name])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # reads the env at import, like a real run
    module.ZizkaDB = fake_client
    with pytest.raises(_Built):
        if hasattr(module, "connect"):  # examples/crewai-agent builds the client here
            module.connect()
        else:
            asyncio.run(module.main())
    return seen


@pytest.mark.parametrize("name", sorted(SCRIPTS))
def test_self_hosted_with_api_key_keeps_the_host(name, monkeypatch):
    kwargs = _client_kwargs(
        name, monkeypatch, {"ZIZKADB_API_KEY": "zizkadb_live_x", "ZIZKADB_HOST": "https://zizka.example.com"}
    )
    assert kwargs == {"api_key": "zizkadb_live_x", "host": "https://zizka.example.com"}


@pytest.mark.parametrize("name", sorted(SCRIPTS))
def test_api_key_alone_means_cloud(name, monkeypatch):
    kwargs = _client_kwargs(name, monkeypatch, {"ZIZKADB_API_KEY": "zizkadb_live_x"})
    assert kwargs["api_key"] == "zizkadb_live_x"
    assert kwargs.get("host") is None


@pytest.mark.parametrize("name", sorted(SCRIPTS))
def test_nothing_set_means_local_stack(name, monkeypatch):
    kwargs = _client_kwargs(name, monkeypatch, {})
    assert kwargs.get("api_key") is None
    assert kwargs["host"] == "http://localhost:8000"

"""Make the sibling `integrations/` packages importable when running the SDK suite.

The LiveKit observer tests live here but the package itself ships from
`integrations/livekit/`, which is not installed in every environment.
"""

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LIVEKIT_PKG = _REPO_ROOT / "integrations" / "livekit"

if _LIVEKIT_PKG.is_dir():
    sys.path.insert(0, str(_LIVEKIT_PKG))


@pytest.fixture(autouse=True)
def _no_host_from_environment(monkeypatch):
    """The client reads ZIZKADB_HOST when no host is passed; a developer's
    exported value must not change which server a test talks to."""
    monkeypatch.delenv("ZIZKADB_HOST", raising=False)
    monkeypatch.delenv("AGENTDB_HOST", raising=False)

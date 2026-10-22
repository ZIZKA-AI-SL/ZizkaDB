"""ZizkaDB observer for LiveKit Agents.

One voice call becomes one ZizkaDB session: transcript turns, tool calls, agent
handoffs and pipeline events, linked by ``parent_id`` so ``why()`` works. No
audio is recorded or stored.

See https://db.zizka.ai/docs and integrations/livekit/README.md.
"""

from zizkadb.telemetry import ping_on_install

from .observer import ZizkaDBLiveKitObserver
from .registry import get_observer, pop_observer, register_observer

__version__ = "0.2.1"

ping_on_install(sdk="livekit")

__all__ = [
    "ZizkaDBLiveKitObserver",
    "__version__",
    "get_observer",
    "pop_observer",
    "register_observer",
]

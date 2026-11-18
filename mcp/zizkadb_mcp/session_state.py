"""In-process MCP session lineage (auto parent_id)."""

from __future__ import annotations

import threading
import uuid

_lock = threading.Lock()
_state: dict[str, str | None] = {
    "session_id": str(uuid.uuid4()),
    "last_event_id": None,
}


def get_session_id() -> str:
    with _lock:
        return _state["session_id"] or str(uuid.uuid4())


def get_last_event_id() -> str | None:
    with _lock:
        return _state["last_event_id"]


def record_event(event_id: str) -> None:
    with _lock:
        _state["last_event_id"] = event_id


def reset_chain() -> None:
    with _lock:
        _state["last_event_id"] = None
        _state["session_id"] = str(uuid.uuid4())

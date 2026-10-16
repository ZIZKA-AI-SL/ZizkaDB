"""Async-safe lineage context for automatic parent_id wiring."""

from __future__ import annotations

import contextvars
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zizkadb.models import LogResult

logger = logging.getLogger(__name__)

MID_CHAIN_EVENT_TYPES = frozenset({
    "tool_call",
    "tool_start",
    "tool_end",
    "tool_result",
    "api_call",
    "llm_end",
    "retrieval",
    "rerank",
    "crew_task",
    "crew_output",
    "graph_node_done",
    "mcp_tool_call",
    "decision",
    "assistant_response",
})

_lineage_ctx: contextvars.ContextVar["LineageState | None"] = contextvars.ContextVar(
    "zizkadb_lineage", default=None
)


def _strict_warnings() -> bool:
    return os.getenv("ZIZKADB_STRICT", "").lower() in ("1", "true", "yes", "on")


@dataclass
class LineageState:
    agent: str
    session_id: str
    last_event_id: str | None = None
    fork_parent_id: str | None = None


class LineageContext:
    """
    Context manager that auto-wires parent_id on log() calls.

    Usage:
        async with db.track(agent="my-bot") as ctx:
            await db.log(agent="my-bot", event="user_message", data={...})
            await db.log(agent="my-bot", event="tool_call", data={...})
    """

    def __init__(self, db: object, agent: str, session_id: str | None = None) -> None:
        self._db = db
        self._agent = agent
        self._session_id = session_id or str(uuid.uuid4())
        self._token: contextvars.Token | None = None
        self._state = LineageState(agent=agent, session_id=self._session_id)

    @property
    def session_id(self) -> str:
        return self._state.session_id

    @property
    def last_event_id(self) -> str | None:
        return self._state.last_event_id

    async def __aenter__(self) -> LineageContext:
        self._token = _lineage_ctx.set(self._state)
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._token is not None:
            _lineage_ctx.reset(self._token)

    def fork(self) -> str | None:
        """Mark next log(s) as branching from current head (parallel tools)."""
        self._state.fork_parent_id = self._state.last_event_id
        return self._state.fork_parent_id

    def end_fork(self) -> None:
        """Resume sequential lineage after parallel branches."""
        self._state.fork_parent_id = None


def get_lineage_state() -> LineageState | None:
    return _lineage_ctx.get()


def resolve_log_lineage(
    *,
    agent: str,
    event: str,
    parent_id: str | None,
    session_id: str | None,
    explicit_parent: bool,
) -> tuple[str | None, str | None]:
    """Resolve parent_id and session_id from active LineageContext."""
    state = _lineage_ctx.get()
    resolved_session = session_id
    resolved_parent = parent_id

    if state is not None and state.agent == agent:
        if resolved_session is None:
            resolved_session = state.session_id
        if not explicit_parent:
            if state.fork_parent_id is not None:
                resolved_parent = state.fork_parent_id
            elif state.last_event_id is not None:
                resolved_parent = state.last_event_id
    elif (
        _strict_warnings()
        and not explicit_parent
        and event in MID_CHAIN_EVENT_TYPES
        and parent_id is None
    ):
        logger.warning(
            "zizkadb: event %r logged without parent_id outside db.track() — "
            "why() chain may be incomplete",
            event,
        )

    return resolved_parent, resolved_session


def record_log_result(agent: str, result: LogResult) -> None:
    state = _lineage_ctx.get()
    if state is not None and state.agent == agent:
        if state.fork_parent_id is None:
            state.last_event_id = result.event_id

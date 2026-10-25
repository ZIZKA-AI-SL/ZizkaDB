"""
ZizkaDB Python SDK dataclasses and model definitions.
"""

import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class Event:
    """Represents a logged agent event in ZizkaDB."""

    event_id: str
    agent: str
    timestamp: datetime
    event: str
    data: dict[str, Any]
    parent_id: str | None = None
    session_id: str | None = None
    sequence_no: int = 0
    score: float | None = None  # set on search results

    @classmethod
    def from_dict(cls, d: dict) -> "Event":
        """Create an Event instance from a dictionary payload."""
        return cls(
            event_id=d["event_id"],
            agent=d["agent"],
            timestamp=datetime.fromisoformat(d["timestamp"]),
            event=d["event"],
            data=d["data"],
            parent_id=d.get("parent_id"),
            session_id=d.get("session_id"),
            sequence_no=d.get("sequence_no", 0),
            score=d.get("score"),
        )

    def __repr__(self) -> str:
        """Return a string representation of the Event."""
        return (
            f"Event(id={self.event_id[:8]}... "
            f"agent={self.agent!r} "
            f"event={self.event!r} "
            f"at={self.timestamp.strftime('%H:%M:%S')})"
        )


@dataclass
class LogResult:
    """Represents the response metadata returned when an event is successfully logged."""

    event_id: str
    timestamp: datetime
    sequence_no: int
    checksum: str
    index_status: str = "skipped"
    chain_hash: str | None = None
    duplicate: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> "LogResult":
        """Create a LogResult instance from a dictionary payload."""
        indexed = d.get("indexed")
        if "index_status" in d:
            index_status = d["index_status"]
        elif indexed is True:
            index_status = "indexed"
        elif indexed is False:
            index_status = "failed"
        else:
            index_status = "skipped"
        return cls(
            event_id=d["event_id"],
            timestamp=datetime.fromisoformat(d["timestamp"]),
            sequence_no=d["sequence_no"],
            checksum=d["checksum"],
            index_status=index_status,
            chain_hash=d.get("chain_hash"),
            duplicate=bool(d.get("duplicate", False)),
        )


@dataclass
class CausalChain:
    """Represents a sequence of causally linked events leading up to a specific event."""

    event_id: str
    chain_length: int
    chain: list[Event]
    chain_complete: bool = True
    orphan: bool = False
    depth_truncated: bool = False
    scoped_agent_limited: bool = False

    def print(self) -> None:
        """Pretty-print the causal chain as a tree."""
        if not self.chain:
            _safe_print("(empty chain)")
            return

        if self.orphan or self.depth_truncated or self.scoped_agent_limited or not self.chain_complete:
            warnings = []
            if self.orphan:
                warnings.append("orphan event (missing parent_id)")
            if self.depth_truncated:
                warnings.append("depth limit reached — chain may continue above")
            if self.scoped_agent_limited:
                warnings.append("agent-scoped key — cross-agent links hidden")
            if warnings:
                _safe_print("⚠ Incomplete chain: " + "; ".join(warnings))

        for i, event in enumerate(self.chain):
            indent = "    " * i
            if i == 0:
                connector = ""
            elif i == len(self.chain) - 1:
                connector = "└── "
            else:
                connector = "├── "
            _safe_print(
                f"{indent}{connector}"
                f"{event.event}: {_truncate(str(event.data), 60)}"
                f"  [{event.timestamp.strftime('%H:%M:%S')}]"
            )

    def __repr__(self) -> str:
        """Return a string representation of the CausalChain."""
        return f"CausalChain(length={self.chain_length})"


@dataclass
class AgentState:
    """Represents the reconstructed state of an agent at a specific timestamp."""

    agent: str
    at: datetime
    event_count: int
    state: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict) -> "AgentState":
        """Create an AgentState instance from a dictionary payload."""
        return cls(
            agent=d["agent"],
            at=datetime.fromisoformat(d["at"]),
            event_count=d["event_count"],
            state=d.get("state", {}),
        )


@dataclass
class AgentInfo:
    """Represents summary information about a registered agent."""

    agent: str
    first_seen: datetime
    last_seen: datetime
    event_count: int

    @classmethod
    def from_dict(cls, d: dict) -> "AgentInfo":
        """Create an AgentInfo instance from a dictionary payload."""
        return cls(
            agent=d["agent"],
            first_seen=datetime.fromisoformat(d["first_seen"]),
            last_seen=datetime.fromisoformat(d["last_seen"]),
            event_count=d["event_count"],
        )


def _truncate(s: str, n: int) -> str:
    """Truncate a string to a maximum length of n, appending ellipses if truncated."""
    return s if len(s) <= n else s[:n] + "..."


def _safe_print(text: str) -> None:
    """
    Print text to stdout while preventing UnicodeEncodeError on consoles (e.g. Windows cmd/PowerShell)
    that do not support UTF-8 characters natively. Falls back to writing UTF-8 encoded bytes directly
    with character replacements, and finally to plain ASCII replacement.
    """
    try:
        print(text)
    except UnicodeEncodeError:
        try:
            sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
            sys.stdout.flush()
        except OSError:
            # Absolute fallback to ASCII
            print(text.encode("ascii", errors="replace").decode("ascii"))


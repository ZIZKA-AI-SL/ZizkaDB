"""
Agent-to-agent handoffs that keep the causal chain across processes,
frameworks and LLMs.

The sending agent records the message with ``db.handoff(...)`` and passes the
returned token along with the message itself (an HTTP header, a queue message
field, a tool argument). The receiving agent, in whatever process, framework or
language, opens ``db.continue_from(token, agent=...)``: its next events link
back to that message, so ``why()`` on the receiver walks into the sender.

The token carries ids only (the message event, its session, the sender and
recipient), never content. It is not a secret: the server still checks that
every linked event belongs to the caller's account.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any

from zizkadb.context import LineageContext

TOKEN_PREFIX = "zdbh1."
HANDOFF_HEADER = "X-ZizkaDB-Handoff"
SENT_EVENT = "a2a.message"
RECEIVED_EVENT = "a2a.received"


@dataclass(frozen=True)
class HandoffToken:
    event_id: str
    session_id: str | None
    from_agent: str
    to_agent: str
    message_id: str | None = None
    # Set when the server already recorded the receipt in the recipient's chain.
    received_event_id: str | None = None

    def encode(self) -> str:
        raw = {"v": 1, "e": self.event_id, "s": self.session_id, "f": self.from_agent, "t": self.to_agent}
        if self.message_id:
            raw["m"] = self.message_id
        if self.received_event_id:
            raw["r"] = self.received_event_id
        body = json.dumps(raw, separators=(",", ":")).encode()
        return TOKEN_PREFIX + base64.urlsafe_b64encode(body).decode().rstrip("=")

    @classmethod
    def decode(cls, token: str) -> HandoffToken:
        if not isinstance(token, str) or not token.startswith(TOKEN_PREFIX):
            raise ValueError("Not a ZizkaDB handoff token (expected 'zdbh1.…')")
        body = token[len(TOKEN_PREFIX):]
        try:
            raw = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError("Malformed ZizkaDB handoff token") from exc
        if not isinstance(raw, dict) or raw.get("v") != 1 or not raw.get("e") or not raw.get("f") or not raw.get("t"):
            raise ValueError("Malformed ZizkaDB handoff token")
        return cls(
            event_id=str(raw["e"]),
            session_id=raw.get("s"),
            from_agent=str(raw["f"]),
            to_agent=str(raw["t"]),
            message_id=raw.get("m"),
            received_event_id=raw.get("r"),
        )


@dataclass(frozen=True)
class Handoff:
    """What ``db.handoff()`` returns: the recorded message and its token."""

    token: str
    event_id: str
    from_agent: str
    to_agent: str
    session_id: str | None
    message_id: str | None = None
    received_event_id: str | None = None
    # "a2a": through /v1/a2a/messages (both sides recorded by the server);
    # "event": logged as the sender's a2a.message event (receiver records its side).
    recorded_by: str = "a2a"
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def headers(self) -> dict[str, str]:
        """HTTP headers that carry the token (``X-ZizkaDB-Handoff``)."""
        return {HANDOFF_HEADER: self.token}


def token_from_headers(headers: Any) -> str | None:
    """The handoff token from incoming request headers, if any (case-insensitive)."""
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if getter is not None:
        value = getter(HANDOFF_HEADER) or getter(HANDOFF_HEADER.lower())
        if value:
            return str(value)
    try:
        for key, value in dict(headers).items():
            if str(key).lower() == HANDOFF_HEADER.lower() and value:
                return str(value)
    except (TypeError, ValueError):
        return None
    return None


class HandoffContext(LineageContext):
    """``async with db.continue_from(token, agent=...)``: events link to the message.

    On entry it records ``a2a.received`` in the receiving agent's chain (caused
    by the sender's message), unless the server already recorded the receipt;
    the events logged inside then follow from it.
    """

    def __init__(self, db: Any, token: str, agent: str, session_id: str | None = None) -> None:
        self.handoff = HandoffToken.decode(token)
        super().__init__(db, agent=agent, session_id=session_id or self.handoff.session_id)
        self.received_event_id: str | None = None

    async def __aenter__(self) -> HandoffContext:
        h = self.handoff
        if h.received_event_id and h.to_agent == self._agent:
            self.received_event_id = h.received_event_id
        else:
            result = await self._db.log(
                agent=self._agent,
                event=RECEIVED_EVENT,
                data={"from_agent": h.from_agent, "message_event_id": h.event_id, "message_id": h.message_id},
                parent_id=h.event_id,
                session_id=self.session_id,
            )
            self.received_event_id = result.event_id
        self._state.last_event_id = self.received_event_id
        await super().__aenter__()
        return self

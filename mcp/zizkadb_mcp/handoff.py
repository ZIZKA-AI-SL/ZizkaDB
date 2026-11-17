"""Handoff tokens, the same format as the SDKs (zizkadb/handoff.py, src/handoff.ts).

``zdbh1.`` + base64url JSON with ids only: the message event, its session, the
sender, the recipient, and the receipt event when the server recorded one.
"""

from __future__ import annotations

import base64
import json

TOKEN_PREFIX = "zdbh1."


def encode_token(event_id: str, session_id: str | None, from_agent: str, to_agent: str,
                 message_id: str | None = None, received_event_id: str | None = None) -> str:
    raw = {"v": 1, "e": event_id, "s": session_id, "f": from_agent, "t": to_agent}
    if message_id:
        raw["m"] = message_id
    if received_event_id:
        raw["r"] = received_event_id
    body = json.dumps(raw, separators=(",", ":")).encode()
    return TOKEN_PREFIX + base64.urlsafe_b64encode(body).decode().rstrip("=")


def decode_token(token: str) -> dict:
    if not isinstance(token, str) or not token.startswith(TOKEN_PREFIX):
        raise ValueError("Not a ZizkaDB handoff token (expected 'zdbh1.…')")
    body = token[len(TOKEN_PREFIX):]
    try:
        raw = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Malformed ZizkaDB handoff token") from exc
    if not isinstance(raw, dict) or raw.get("v") != 1 or not raw.get("e") or not raw.get("f") or not raw.get("t"):
        raise ValueError("Malformed ZizkaDB handoff token")
    return raw

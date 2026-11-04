"""The one rule for agent ids, shared by the write path and /v1/agents/{id} routes.

An agent created on write must be reachable through every agent route, so both
sides validate against the same pattern (the routes trim surrounding spaces first;
the write path rejects them).
"""

from __future__ import annotations

import re

AGENT_ID_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,254}")
AGENT_ID_RULE = "1–255 chars: letters, numbers, dots, dashes, underscores, starting with a letter or number"


def is_valid_agent_id(agent_id: str) -> bool:
    # fullmatch, not match with "$": "$" also matches before a trailing newline.
    return AGENT_ID_RE.fullmatch(agent_id) is not None


def is_storable(agent_id: str) -> bool:
    """False for text Postgres cannot store (NUL, unpaired surrogates), so no
    agent with that name can exist and looking it up would fail."""
    if "\x00" in agent_id:
        return False
    try:
        agent_id.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True

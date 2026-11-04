"""Error text that is safe to store and log.

An exception message can carry credentials: an ``Authorization`` header, a
provider or ZizkaDB API key, a connection URL with its password. Text stored in
the database (``events.index_last_error``) or written to the log goes through
``safe_error_text()``, which masks them and keeps the text short.

This is for error messages only. Event payloads are redacted by
``services/redaction.py`` before they are stored.
"""

from __future__ import annotations

import re

# Longest text returned.
MAX_CHARS = 500

# Only the start of a very long message is scanned, so the work stays bounded.
# Generous on purpose: messages are masked in full, then capped, so no cut can
# leave part of a secret; this guard only matters for pathological messages.
SCAN_CHARS = 256 * 1024

_REDACTED = "[redacted]"

# Applied in order. Each keeps the label and masks the value.
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Authorization header, with its scheme: "Authorization: Bearer <token>".
    (
        re.compile(r"""(?i)(\bauthorization["']?\s*[:=]\s*["']?)(?:(?:bearer|basic|token)\s+)?[^\s"',;]+"""),
        r"\1" + _REDACTED,
    ),
    # A bearer or basic credential elsewhere. A word of only lowercase letters
    # is prose ("missing bearer token", "basic validation"), not a credential.
    (
        re.compile(r"\b((?i:bearer|basic))\s+(?![a-z]+\b)[A-Za-z0-9._~+/=-]{8,}"),
        r"\1 " + _REDACTED,
    ),
    # Credential fields in headers, JSON or key=value text.
    (
        re.compile(
            r"""(?i)(["']?\b(?:x-api-key|api[-_]?key|apikey|(?:access|refresh|auth|id)[-_]?token"""
            r"""|client[-_]?secret|secret[-_]?key|password|passwd|pwd)["']?\s*[:=]\s*["']?)"""
            r"""[^\s"',;&}]+"""
        ),
        r"\1" + _REDACTED,
    ),
    # Credential query parameters: ?key=..., &token=...
    (
        re.compile(r"""(?i)([?&](?:key|token|secret|sig|signature|password)=)[^&\s"'#]+"""),
        r"\1" + _REDACTED,
    ),
    # Provider keys (sk-..., sk-proj-...) and ZizkaDB API keys (zizkadb_live_...,
    # legacy agdb_...).
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"), _REDACTED),
    (re.compile(r"\b(?:zizkadb|agdb)_[A-Za-z0-9_-]{8,}"), _REDACTED),
    # Passwords in URLs, also with an empty user (redis://:password@host).
    (re.compile(r"://[^/\s:@]*:[^/\s@]+@"), "://" + _REDACTED + "@"),
)


def mask_secrets(text: str) -> str:
    """``text`` with the credentials ``_SECRET_PATTERNS`` recognises masked."""
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def safe_error_text(exc: BaseException) -> str:
    """``exc`` as one short line that is safe to store and log.

    The exception type and message, on one line of printable characters
    (Postgres TEXT refuses NUL bytes), with credentials masked, then capped at
    ``MAX_CHARS``. Masking runs on the whole message before the cap, so a cut
    never leaves part of a secret.
    """
    message = str(exc)
    text = f"{type(exc).__name__}: {message}" if message else type(exc).__name__
    if len(text) > SCAN_CHARS:
        # Drop the word the guard cuts through: it could be part of a secret
        # too short for the patterns to recognise.
        end = SCAN_CHARS
        while end and text[end - 1].isprintable() and not text[end - 1].isspace():
            end -= 1
        text = text[:end] + " ..."
    text = "".join(ch if ch.isprintable() else " " for ch in text)
    text = " ".join(mask_secrets(text).split())
    if len(text) > MAX_CHARS:
        text = text[: MAX_CHARS - 3] + "..."
    return text

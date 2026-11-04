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

# Characters a token or key is made of.
_TOKEN_CHAR = r"[A-Za-z0-9._~+/=-]"

# Field names whose value is a credential, in headers, JSON or key=value text
# and query strings. A prefix joined by - or _ is allowed (x-api-key,
# aws_secret_access_key, refresh_token, Set-Cookie).
_SECRET_FIELD = (
    r"(?:[A-Za-z0-9]+[-_])*"
    r"(?:api[-_]?key|apikey|key|access[-_]?key(?:[-_]?id)?|private[-_]?key|secret(?:[-_]?key)?"
    r"|token|password|passwd|pwd|credentials?|session(?:[-_]?(?:id|token))?|cookie|signature|sig)"
)


def _mask_field_value(match: re.Match[str]) -> str:
    value = match.group(2)
    if value[:1] in ("'", '"'):  # quoted: mask all of it, keep the quotes
        return match.group(1) + value[0] + _REDACTED + value[0]
    return match.group(1) + _REDACTED


# Applied in order.
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], object], ...] = (
    # Authorization header, with its scheme: "Authorization: Bearer <token>".
    (
        re.compile(r"""(?i)(\b(?:proxy-)?authorization["']?\s*[:=]\s*["']?)(?:(?:bearer|basic|token)\s+)?[^\s"',;]+"""),
        r"\1" + _REDACTED,
    ),
    # A bearer or basic credential elsewhere. A word of only lowercase letters
    # is prose ("missing bearer token", "basic validation"), not a credential;
    # "abcdefgh-123..." is a credential.
    (
        re.compile(r"\b((?i:bearer|basic))\s+(?![a-z]+(?!" + _TOKEN_CHAR + r"))" + _TOKEN_CHAR + r"{8,}"),
        r"\1 " + _REDACTED,
    ),
    # Credential fields; a quoted value is masked whole, spaces included.
    (
        re.compile(r"""(?i)(\b""" + _SECRET_FIELD + r"""["']?\s*[:=]\s*)("[^"]*"|'[^']*'|[^\s"',;&}]+)"""),
        _mask_field_value,
    ),
    # Provider keys (sk-..., sk-proj-...), also after "_" or "=", and ZizkaDB
    # API keys (zizkadb_live_..., legacy agdb_...) wherever they appear.
    (re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{8,}"), _REDACTED),
    (re.compile(r"(?:zizkadb|agdb)_[A-Za-z0-9_-]{8,}"), _REDACTED),
    # Credentials in URLs: everything before the last "@" of the URL, so a
    # password containing "@" or "/" is masked whole (redis://:pw@host too).
    (re.compile(r"://\S*@"), "://" + _REDACTED + "@"),
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

"""Redact secrets and personal data from event data before it is stored.

Runs in the write path before the checksum, so what is stored, hashed and
indexed is the redacted value; the original never reaches the database.

- Keys (on by default): values under keys that name a secret (``password``,
  ``api_key``, ``authorization``, ...) become ``[REDACTED]``. Extend with
  ``REDACT_KEYS`` (comma-separated, case-insensitive); ``REDACT_KEYS=off``
  turns key redaction off.
- Patterns (opt-in): ``REDACT_PATTERNS=email,phone,card,iban,ipv4`` replaces
  matches inside any string with ``[REDACTED:<kind>]``. Card numbers must pass
  the Luhn check, so order ids and timestamps are left alone.
"""

from __future__ import annotations

import os
import re
from typing import Any

DEFAULT_SECRET_KEYS = frozenset({
    "password", "passwd", "pwd", "secret", "client_secret", "api_key", "apikey",
    "access_token", "refresh_token", "id_token", "auth_token", "authorization",
    "private_key", "secret_key", "session_token", "cookie", "set-cookie",
})

_PATTERNS: dict[str, re.Pattern] = {
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "phone": re.compile(r"(?<![\w+])\+?\d[\d\s().-]{7,}\d(?!\w)"),
    "card": re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)"),
    "iban": re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b"),
    "ipv4": re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"),
}


def _luhn_ok(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _settings() -> tuple[frozenset[str], list[str]]:
    raw_keys = (os.getenv("REDACT_KEYS") or "").strip()
    if raw_keys.lower() == "off":
        keys: frozenset[str] = frozenset()
    else:
        extra = {k.strip().lower() for k in raw_keys.split(",") if k.strip()}
        keys = DEFAULT_SECRET_KEYS | extra
    patterns = [
        p.strip().lower()
        for p in (os.getenv("REDACT_PATTERNS") or "").split(",")
        if p.strip().lower() in _PATTERNS
    ]
    return keys, patterns


def _redact_string(value: str, patterns: list[str], counter: list[int]) -> str:
    for kind in patterns:
        def repl(m: re.Match, kind=kind) -> str:
            if kind == "card" and not _luhn_ok(re.sub(r"\D", "", m.group(0))):
                return m.group(0)
            counter[0] += 1
            return f"[REDACTED:{kind}]"

        value = _PATTERNS[kind].sub(repl, value)
    return value


def redact(value: Any) -> tuple[Any, int]:
    """Return (redacted copy, number of redactions). Input is not modified."""
    keys, patterns = _settings()
    if not keys and not patterns:
        return value, 0
    counter = [0]

    def walk(v: Any) -> Any:
        if isinstance(v, dict):
            out = {}
            for k, item in v.items():
                if isinstance(k, str) and k.lower() in keys and item not in (None, ""):
                    counter[0] += 1
                    out[k] = "[REDACTED]"
                else:
                    out[k] = walk(item)
            return out
        if isinstance(v, list):
            return [walk(i) for i in v]
        if isinstance(v, str) and patterns:
            return _redact_string(v, patterns, counter)
        return v

    return walk(value), counter[0]

"""Redaction of secrets (default) and personal data (opt-in) before storage."""

import pytest
from fastapi import HTTPException

from services import event_write
from services.redaction import redact


def test_secret_keys_are_redacted_by_default(monkeypatch):
    monkeypatch.delenv("REDACT_KEYS", raising=False)
    monkeypatch.delenv("REDACT_PATTERNS", raising=False)
    data = {"tool": "login", "args": {"username": "ann", "Password": "hunter2"},
            "headers": [{"Authorization": "Bearer x"}], "api_key": None}
    out, n = redact(data)
    assert out["args"] == {"username": "ann", "Password": "[REDACTED]"}
    assert out["headers"][0]["Authorization"] == "[REDACTED]"
    assert out["api_key"] is None  # nothing to hide
    assert n == 2
    assert data["args"]["Password"] == "hunter2"  # input untouched


def test_patterns_are_opt_in(monkeypatch):
    monkeypatch.delenv("REDACT_PATTERNS", raising=False)
    assert redact({"t": "mail ann@example.com"}) == ({"t": "mail ann@example.com"}, 0)

    monkeypatch.setenv("REDACT_PATTERNS", "email,card,phone,ipv4,iban")
    out, n = redact({"t": "ann@example.com paid 4111 1111 1111 1111 from 10.0.0.7, "
                          "call +44 20 7946 0958, IBAN DE89370400440532013000"})
    assert out["t"] == ("[REDACTED:email] paid [REDACTED:card] from [REDACTED:ipv4], "
                        "call [REDACTED:phone], IBAN [REDACTED:iban]")
    assert n == 5


def test_card_needs_luhn(monkeypatch):
    monkeypatch.setenv("REDACT_PATTERNS", "card")
    assert redact({"order": "order 1234567812345678"})[1] == 0


def test_custom_keys_and_off(monkeypatch):
    monkeypatch.setenv("REDACT_KEYS", "ssn")
    assert redact({"SSN": "123", "password": "x"})[1] == 2
    monkeypatch.setenv("REDACT_KEYS", "off")
    assert redact({"password": "x"}) == ({"password": "x"}, 0)


async def test_write_path_redacts_before_storage(monkeypatch):
    """The size check sees the redacted payload: a long password does not count."""
    class Reached(Exception):
        pass

    def stop():
        raise Reached

    monkeypatch.setattr(event_write, "MAX_EVENT_BYTES", 40)
    monkeypatch.setattr(event_write, "get_pool", stop)
    with pytest.raises(Reached):
        await event_write.write_event(tenant_id="t", agent="bot", event="e",
                                      data={"password": "p" * 100})
    with pytest.raises(HTTPException):
        await event_write.write_event(tenant_id="t", agent="bot", event="e",
                                      data={"note": "n" * 100})

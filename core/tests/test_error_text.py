"""safe_error_text(): error messages that are safe to store and log."""

import time

import pytest

from services import error_text
from services.error_text import safe_error_text


@pytest.mark.parametrize(
    "message,secret",
    [
        ("Authorization: Bearer eyJhbGciOi.payload.sig", "eyJhbGciOi.payload.sig"),
        ('{"authorization": "Bearer abc123secretvalue"}', "abc123secretvalue"),
        ("authorization: basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
        ("token was Bearer eyJhbGciOiJIUzI1NiJ9", "eyJhbGciOiJIUzI1NiJ9"),
        ("invalid key sk-proj-AbCdEf0123456789", "sk-proj-AbCdEf0123456789"),
        ("key zizkadb_live_Q2x9aBcDeFgHiJkLmN", "zizkadb_live_Q2x9aBcDeFgHiJkLmN"),
        ("legacy agdb_live_ZZZZZZZZZZZZ", "agdb_live_ZZZZZZZZZZZZ"),
        ("GET https://h/v1?model=m&api_key=S3cr3tValue&x=1", "S3cr3tValue"),
        ("GET https://h/v1?key=S3cr3tValue", "S3cr3tValue"),
        ("GET https://h/v1?token=S3cr3tValue#frag", "S3cr3tValue"),
        ("qdrant said api-key: abc123secret", "abc123secret"),
        ("header x-api-key=abc123secret refused", "abc123secret"),
        ('body {"api_key": "abc123secret", "model": "m"}', "abc123secret"),
        ("client_secret='abc123secret'", "abc123secret"),
        ("password: hunter2", "hunter2"),
        ("connect postgresql://admin:hunter2@db:5432/z", "hunter2"),
        ("connect redis://:hunter2@redis:6379", "hunter2"),
    ],
)
def test_credentials_are_masked(message, secret):
    text = safe_error_text(RuntimeError(message))
    assert secret not in text
    assert "[redacted]" in text


@pytest.mark.parametrize(
    "message",
    ["missing bearer token", "basic validation failed for field x", "the key is missing"],
)
def test_prose_is_not_masked(message):
    assert safe_error_text(ValueError(message)) == f"ValueError: {message}"


def test_masked_text_keeps_its_labels():
    text = safe_error_text(RuntimeError("POST https://h/v1?model=m&api_key=S3cr3t failed: 401"))
    assert text == "RuntimeError: POST https://h/v1?model=m&api_key=[redacted] failed: 401"


def test_one_printable_capped_line():
    text = safe_error_text(RuntimeError("a\nb\x00c\td\r\n" + "word " * 400))
    assert text.startswith("RuntimeError: a b c d word word")
    assert text.isprintable()
    assert len(text) == error_text.MAX_CHARS
    assert text.endswith("...")


def test_masks_before_capping():
    """A key cut by the cap would be too short to match: mask first."""
    # "RuntimeError: " + 474 + " " + "sk-" puts 5 key characters before the cut.
    text = safe_error_text(RuntimeError("A" * 474 + " sk-QWERTYUIOPASDFGH"))
    assert "QWERT" not in text


def test_a_long_message_without_spaces_keeps_its_start():
    """Masking runs on the whole message, so nothing but the cap shortens it."""
    url = "https://example.com/" + "a" * 3000
    text = safe_error_text(RuntimeError(f"bad url {url}"))
    assert text.startswith("RuntimeError: bad url https://example.com/aaa")
    assert len(text) == error_text.MAX_CHARS


def test_a_secret_cut_by_the_scan_guard_is_dropped():
    """Whitespace collapses after the guard, so a key fragment at its edge
    would otherwise land inside the returned text."""
    room = error_text.SCAN_CHARS - len("RuntimeError: ")
    text = safe_error_text(RuntimeError(" " * (room - 6) + "sk-ABCDEFGHIJKLMNOP"))
    assert "sk-ABC" not in text
    assert text == "RuntimeError: ..."


def test_work_is_bounded_for_huge_messages():
    message = ("x" * 50 + " ://a:b sk-" + "Q" * 30 + " ") * 200_000  # ~18 MB
    started = time.perf_counter()
    text = safe_error_text(RuntimeError(message))
    assert time.perf_counter() - started < 2.0
    assert len(text) <= error_text.MAX_CHARS
    assert "QQQQQQQQ" not in text


def test_an_exception_without_a_message_is_its_type():
    assert safe_error_text(TimeoutError()) == "TimeoutError"

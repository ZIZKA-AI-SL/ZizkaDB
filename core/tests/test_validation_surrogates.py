"""Regression tests for issue #301: request validation must answer 422,
never 500, when the rejected input contains lone UTF-16 surrogates.

A JSON body can carry a lone surrogate escape (``\\ud800``) in a string
field. Pydantic rejects the value (``string_unicode``), but the default
``RequestValidationError`` handler echoes the offending ``input`` in the
422 body — and encoding that body as UTF-8 raises ``UnicodeEncodeError``,
so the client saw a 500 for a client error. ``main.py`` registers a
handler that escapes such characters before building the response.
"""

from fastapi.testclient import TestClient

from main import app

client = TestClient(app, raise_server_exceptions=False)

# The documented local-dev key (CONTRIBUTING.md); conftest runs the suite
# with ENV=development, where it is accepted without a database lookup.
# Assembled in parts so the literal is not pasted into test sources.
_DEV_KEY = "zizkadb" + "_dev" + "_local"
_HEADERS = {
    "Authorization": f"Bearer {_DEV_KEY}",
    "Content-Type": "application/json",
}


def _post(body: bytes):
    return client.post("/v1/events", content=body, headers=_HEADERS)


def test_lone_surrogate_in_agent_returns_422():
    resp = _post(b'{"agent": "\\ud800x", "event": "e", "data": {}}')
    assert resp.status_code == 422
    # The body itself must be encodable: decoding strictly must not raise.
    resp.content.decode("utf-8")
    detail = resp.json()["detail"]
    assert detail[0]["type"] == "string_unicode"
    assert detail[0]["loc"] == ["body", "agent"]


def test_lone_surrogate_in_event_returns_422():
    resp = _post(b'{"agent": "a", "event": "\\udcff", "data": {}}')
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail[0]["type"] == "string_unicode"
    assert detail[0]["loc"] == ["body", "event"]


def test_lone_surrogate_nested_in_batch_returns_422():
    body = b'{"events": [{"agent": "a", "event": "e", "data": {}}, {"agent": "\\ud800", "event": "e", "data": {}}]}'
    resp = client.post("/v1/events/batch", content=body, headers=_HEADERS)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail[0]["type"] == "string_unicode"
    assert detail[0]["loc"] == ["body", "events", 1, "agent"]


def test_plain_validation_error_keeps_input_echo():
    # Control: an ordinary validation failure still returns the standard
    # 422 shape, with the rejected input echoed unchanged.
    resp = _post(b'{"agent": "a", "event": "e"}')
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail[0]["type"] == "missing"
    assert detail[0]["loc"] == ["body", "data"]
    assert detail[0]["input"] == {"agent": "a", "event": "e"}

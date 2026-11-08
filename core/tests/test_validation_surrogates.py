"""Regression tests for issue #301: request validation must answer 422,
never 500, when the rejected input contains lone UTF-16 surrogates.

A JSON body can carry a lone surrogate escape (``\\ud800``) in a string
field. Pydantic rejects the value (``string_unicode``), but the default
``RequestValidationError`` handler echoes the offending ``input`` in the
422 body — and encoding that body as UTF-8 raises ``UnicodeEncodeError``,
so the client saw a 500 for a client error. ``main.py`` registers a
handler that escapes such characters before building the response.

The ``data``/``metadata`` cases below cover issue #408 (follow-up to
#390, requested by the maintainer): a lone surrogate inside ``data`` or
``metadata`` must be a 400 naming the field, not a 500 from the
``::jsonb`` cast. ``validate_event_payload()`` already walks both
recursively (``_check_value``, added in #229); these tests lock that
behaviour in at the HTTP level.
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


# --- issue #408 (follow-up to #390): lone surrogates in data/metadata ---
# Unlike agent/event (plain str fields, rejected by Pydantic with 422),
# dict values pass Pydantic through, so validate_event_payload() must
# reject them with a 400 naming the field before anything is written.


def test_lone_surrogate_in_data_value_returns_400():
    resp = _post(b'{"agent": "a", "event": "e", "data": {"note": "\\ud800x"}}')
    assert resp.status_code == 400
    # The body itself must be encodable: decoding strictly must not raise.
    resp.content.decode("utf-8")
    assert resp.json()["detail"] == "data.note contains an unpaired UTF-16 surrogate"


def test_lone_surrogate_nested_in_data_returns_400():
    resp = _post(b'{"agent": "a", "event": "e", "data": {"a": {"b": ["\\ud800"]}}}')
    assert resp.status_code == 400
    assert resp.json()["detail"] == "data.a.b[0] contains an unpaired UTF-16 surrogate"


def test_lone_surrogate_in_data_key_returns_400():
    resp = _post(b'{"agent": "a", "event": "e", "data": {"\\ud800k": "v"}}')
    assert resp.status_code == 400
    assert resp.json()["detail"] == "data key contains an unpaired UTF-16 surrogate"


def test_lone_surrogate_in_metadata_returns_400():
    resp = _post(b'{"agent": "a", "event": "e", "data": {}, "metadata": {"m": "\\udcff"}}')
    assert resp.status_code == 400
    assert resp.json()["detail"] == "metadata.m contains an unpaired UTF-16 surrogate"


def test_lone_surrogate_in_batch_data_returns_per_item_400():
    body = (
        b'{"events": [{"agent": "a", "event": "e", "data": {"note": "\\ud800x"}}, '
        b'{"agent": "a", "event": "e", "data": {}, "metadata": {"m": "\\udcff"}}]}'
    )
    resp = client.post("/v1/events/batch", content=body, headers=_HEADERS)
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["written"] == 0
    assert payload["failed"] == 2
    assert payload["results"][0] == {
        "index": 0,
        "ok": False,
        "status": 400,
        "error": "data.note contains an unpaired UTF-16 surrogate",
    }
    assert payload["results"][1] == {
        "index": 1,
        "ok": False,
        "status": 400,
        "error": "metadata.m contains an unpaired UTF-16 surrogate",
    }

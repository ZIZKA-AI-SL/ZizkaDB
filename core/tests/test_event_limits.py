"""Event size cap (MAX_EVENT_BYTES) and the request body limit (MAX_REQUEST_BYTES)."""

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from middleware.body_limit import BodyLimitMiddleware
from services import event_write


async def test_oversized_event_is_413_before_any_db_work(monkeypatch):
    monkeypatch.setattr(event_write, "MAX_EVENT_BYTES", 100)
    monkeypatch.setattr(event_write, "get_pool", lambda: pytest.fail("touched the DB"))
    with pytest.raises(HTTPException) as exc:
        await event_write.write_event(
            tenant_id="t", agent="bot", event="e", data={"blob": "x" * 200}
        )
    assert exc.value.status_code == 413
    assert "MAX_EVENT_BYTES" in exc.value.detail


async def test_metadata_counts_towards_the_cap(monkeypatch):
    monkeypatch.setattr(event_write, "MAX_EVENT_BYTES", 100)
    monkeypatch.setattr(event_write, "get_pool", lambda: pytest.fail("touched the DB"))
    with pytest.raises(HTTPException) as exc:
        await event_write.write_event(
            tenant_id="t", agent="bot", event="e", data={}, metadata={"m": "y" * 200}
        )
    assert exc.value.status_code == 413


@pytest.mark.parametrize("key", ["", "k" * 256])
async def test_bad_idempotency_key_is_400(key):
    with pytest.raises(HTTPException) as exc:
        await event_write.write_event(
            tenant_id="t", agent="bot", event="e", data={}, idempotency_key=key
        )
    assert exc.value.status_code == 400


def _app(limit: int) -> TestClient:
    app = FastAPI()

    @app.post("/echo")
    async def echo(request: Request):
        return {"n": len(await request.body())}

    app.add_middleware(BodyLimitMiddleware, max_bytes=limit)
    return TestClient(app)


def test_body_under_the_limit_passes():
    assert _app(100).post("/echo", content=b"x" * 100).json() == {"n": 100}


def test_content_length_over_the_limit_is_413():
    r = _app(100).post("/echo", content=b"x" * 101)
    assert r.status_code == 413
    assert "MAX_REQUEST_BYTES" in r.json()["detail"]


def test_chunked_body_over_the_limit_is_413():
    def chunks():
        for _ in range(5):
            yield b"x" * 50

    assert _app(100).post("/echo", content=chunks()).status_code == 413

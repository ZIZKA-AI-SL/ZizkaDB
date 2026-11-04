"""Refuse request bodies over MAX_REQUEST_BYTES with a 413, before they are parsed.

Content-Length is checked up front; a chunked body without one is counted as it
streams in. Per-event limits (MAX_EVENT_BYTES) live in services/event_write.py.
"""

from __future__ import annotations

import json
import os

DEFAULT_MAX_REQUEST_BYTES = 10 * 1024 * 1024


class _TooLarge(Exception):
    pass


class BodyLimitMiddleware:
    def __init__(self, app, max_bytes: int | None = None) -> None:
        self.app = app
        self.max_bytes = max_bytes or int(
            os.getenv("MAX_REQUEST_BYTES", str(DEFAULT_MAX_REQUEST_BYTES))
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    if int(value) > self.max_bytes:
                        return await self._reject(send)
                except ValueError:
                    pass
                break

        seen = 0
        started = False

        async def limited_receive():
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    raise _TooLarge
            return message

        async def tracking_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _TooLarge:
            if not started:
                await self._reject(send)

    async def _reject(self, send) -> None:
        body = json.dumps({
            "detail": f"Request body is larger than {self.max_bytes} bytes (MAX_REQUEST_BYTES)."
        }).encode()
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})

"""Attach X-Request-ID to every API response and log request start/end."""

from __future__ import annotations

import logging
import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"
_SAFE_REQUEST_ID_RE = re.compile(r"^[a-zA-Z0-9_\-\.]{1,64}$")

log = logging.getLogger(__name__)


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        incoming = (request.headers.get(REQUEST_ID_HEADER) or "").strip()
        if incoming and _SAFE_REQUEST_ID_RE.match(incoming):
            request_id = incoming
        else:
            request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        start = time.perf_counter()
        log.info("[%s] %s %s", request_id, request.method, request.url.path)

        response = await call_next(request)

        duration_ms = (time.perf_counter() - start) * 1000
        log.info(
            "[%s] %s %s -> %s %.1fms",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

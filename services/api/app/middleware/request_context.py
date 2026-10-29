"""Request correlation and idempotency metadata middleware."""

from __future__ import annotations

import re
from uuid import UUID, uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_TRACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,255}$")


def _safe_header(value: str | None, pattern: re.Pattern[str]) -> str | None:
    if value is None or not pattern.fullmatch(value):
        return None
    return value


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Set a trace ID for every request and preserve valid caller correlation IDs."""

    async def dispatch(self, request: Request, call_next) -> Response:
        requested_trace = _safe_header(request.headers.get("x-trace-id"), _TRACE_ID_PATTERN)
        request.state.trace_id = requested_trace or str(uuid4())
        request.state.idempotency_key = _safe_header(
            request.headers.get("idempotency-key"), _IDEMPOTENCY_PATTERN
        )
        response = await call_next(request)
        response.headers["X-Trace-ID"] = request.state.trace_id
        return response

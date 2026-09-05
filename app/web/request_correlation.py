"""HTTP request correlation for logs, responses, and support evidence."""

from __future__ import annotations

import logging
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.datastructures import MutableHeaders

from app.utils.logger import bind_request_id, reset_request_id


ASGIApp = Callable[
    [dict[str, Any], Callable[..., Awaitable[Any]], Callable[..., Awaitable[Any]]],
    Awaitable[None],
]

_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


class RequestCorrelationMiddleware:
    """Assign a safe request ID and include it in HTTP logs and responses."""

    def __init__(self, app: ASGIApp, *, logger: logging.Logger) -> None:
        self.app = app
        self._logger = logger

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request_id = _request_id(scope)
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        token = bind_request_id(request_id)
        started = time.perf_counter()
        status_code: int | None = None

        async def send_with_request_id(message) -> None:
            nonlocal status_code
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status", 500))
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        method = str(scope.get("method", "HTTP"))
        path = str(scope.get("path", "/"))
        self._logger.info("HTTP request: %s %s", method, path)
        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            self._logger.exception("HTTP request failed: %s %s", method, path)
            raise
        finally:
            duration_ms = round((time.perf_counter() - started) * 1_000)
            self._logger.info(
                "HTTP response: %s %s status=%s duration_ms=%s",
                method,
                path,
                status_code if status_code is not None else "unhandled",
                duration_ms,
            )
            reset_request_id(token)


def _request_id(scope: dict[str, Any]) -> str:
    for raw_name, raw_value in scope.get("headers", ()):
        if bytes(raw_name).lower() != b"x-request-id":
            continue
        candidate = bytes(raw_value).decode("ascii", errors="ignore").strip()
        if _SAFE_REQUEST_ID.fullmatch(candidate):
            return candidate
        break
    return secrets.token_hex(16)

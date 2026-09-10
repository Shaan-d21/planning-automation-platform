"""Compatibility bridge for APIs migrating to the version 1 contract."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from starlette.datastructures import MutableHeaders


ASGIApp = Callable[
    [dict[str, Any], Callable[..., Awaitable[Any]], Callable[..., Awaitable[Any]]],
    Awaitable[None],
]


class ApiVersioningMiddleware:
    """Expose migrated feature APIs under ``/api/v1``.

    The existing endpoint implementations remain the single source of truth
    while clients move to the versioned contract. Requests to an old URL keep
    working temporarily and receive deprecation metadata.
    """

    _MIGRATED_ROOTS = (
        "agent",
        "data-explorer",
        "data-review",
        "health",
        "reports",
        "schedules",
        "substitution-variables",
        "user-variables",
        "uploads",
    )

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        original_path = str(scope.get("path", ""))
        target_path = self._legacy_target(original_path)
        legacy_successor = self._versioned_successor(original_path)
        if target_path is not None:
            scope = dict(scope)
            scope["path"] = target_path
            scope["raw_path"] = target_path.encode("utf-8")

        async def send_with_contract_headers(message) -> None:
            if message.get("type") == "http.response.start":
                headers = MutableHeaders(scope=message)
                if target_path is not None:
                    headers["X-API-Version"] = "1"
                elif legacy_successor is not None:
                    headers["Deprecation"] = "true"
                    headers["Link"] = (
                        f'<{legacy_successor}>; rel="successor-version"'
                    )
            await send(message)

        await self.app(scope, receive, send_with_contract_headers)

    @classmethod
    def _legacy_target(cls, path: str) -> str | None:
        prefix = "/api/v1/"
        if not path.startswith(prefix):
            return None
        suffix = path[len(prefix) :]
        if cls._is_migrated_suffix(suffix):
            return f"/api/{suffix}"
        if suffix.startswith("operations/"):
            return f"/api/{suffix}"
        return None

    @classmethod
    def _versioned_successor(cls, path: str) -> str | None:
        prefix = "/api/"
        if not path.startswith(prefix) or path.startswith("/api/v1/"):
            return None
        suffix = path[len(prefix) :]
        if cls._is_migrated_suffix(suffix) or suffix.startswith("operations/"):
            return f"/api/v1/{suffix}"
        return None

    @classmethod
    def _is_migrated_suffix(cls, suffix: str) -> bool:
        return any(
            suffix == root or suffix.startswith(f"{root}/")
            for root in cls._MIGRATED_ROOTS
        )

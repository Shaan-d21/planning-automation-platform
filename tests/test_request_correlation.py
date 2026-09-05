"""Tests for safe HTTP correlation IDs and response evidence."""

from __future__ import annotations

import logging
import re

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.web.request_correlation import RequestCorrelationMiddleware


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        RequestCorrelationMiddleware,
        logger=logging.getLogger("oracle_planning_automation.test_http"),
    )

    @app.get("/context")
    async def context(request: Request):
        return {"request_id": request.state.request_id}

    @app.get("/rejected")
    async def rejected():
        raise HTTPException(status_code=400, detail="Rejected for testing.")

    return app


def test_request_correlation_generates_and_returns_safe_identifier() -> None:
    response = TestClient(_app()).get("/context")

    request_id = response.headers["X-Request-ID"]
    assert re.fullmatch(r"[0-9a-f]{32}", request_id)
    assert response.json()["request_id"] == request_id


def test_request_correlation_preserves_valid_upstream_identifier() -> None:
    response = TestClient(_app()).get(
        "/context",
        headers={"X-Request-ID": "gateway:forecast-2026.08"},
    )

    assert response.headers["X-Request-ID"] == "gateway:forecast-2026.08"
    assert response.json()["request_id"] == "gateway:forecast-2026.08"


def test_request_correlation_replaces_unsafe_identifier_and_covers_errors() -> None:
    client = TestClient(_app())
    replaced = client.get("/context", headers={"X-Request-ID": "unsafe value"})
    rejected = client.get("/rejected")

    assert replaced.headers["X-Request-ID"] != "unsafe value"
    assert re.fullmatch(r"[0-9a-f]{32}", replaced.headers["X-Request-ID"])
    assert rejected.status_code == 400
    assert re.fullmatch(r"[0-9a-f]{32}", rejected.headers["X-Request-ID"])

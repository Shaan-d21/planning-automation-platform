"""Shared browser-session and authorization policies for web API versions."""

from __future__ import annotations

import secrets
from uuid import uuid4

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

from app.models.access_control import Permission, UserAccount
from app.models.api_token import ApiTokenScope, AuthenticatedApiToken
from app.utils.exceptions import ApiTokenError


def current_user(request: Request) -> UserAccount | None:
    """Resolve the active platform user represented by the signed session."""
    raw_user_id = request.session.get("user_id")
    if raw_user_id is None:
        return None
    try:
        user = request.app.state.access_control.get_user(int(raw_user_id))
    except (TypeError, ValueError):
        user = None
    if user is None or not user.active:
        request.session.clear()
        return None
    request.state.current_user = user
    return user


def csrf_token(request: Request) -> str:
    """Return the session CSRF token, creating one when necessary."""
    token = str(request.session.get("csrf_token", "")).strip()
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def validate_csrf(request: Request) -> None:
    """Validate CSRF protection for every state-changing browser request."""
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    expected = str(request.session.get("csrf_token", ""))
    supplied = str(request.headers.get("X-CSRF-Token", ""))
    if not expected or not secrets.compare_digest(expected, supplied):
        raise HTTPException(
            status_code=403,
            detail=(
                "The security token is missing or expired. Refresh the page "
                "and try again."
            ),
        )


def require_permission(request: Request, user: UserAccount) -> None:
    """Enforce the capabilities assigned to one protected product surface."""
    required = required_permissions(request.method, request.url.path)
    if required and not any(user.has_permission(item) for item in required):
        raise HTTPException(
            status_code=403,
            detail="Your platform role does not permit this action.",
        )


def require_page_session(request: Request) -> RedirectResponse | None:
    """Protect a server-rendered page while preserving legacy redirects."""
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    try:
        require_permission(request, user)
    except HTTPException:
        return RedirectResponse("/app?access=denied", status_code=303)
    return None


def require_api_session(request: Request) -> str:
    """Protect a JSON endpoint and return its upload/session owner token."""
    user = current_user(request)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Sign in to the automation platform to continue.",
        )
    validate_csrf(request)
    require_permission(request, user)
    session_id = str(request.session.get("session_id", "")).strip()
    if not session_id:
        raise HTTPException(
            status_code=401,
            detail="The web session is invalid. Connect again.",
        )
    return session_id


def require_bearer_token(
    request: Request,
    *,
    scope: ApiTokenScope,
    permission: Permission,
) -> AuthenticatedApiToken:
    """Authenticate a non-browser client with scope and role enforcement."""
    authorization = str(request.headers.get("Authorization", "")).strip()
    scheme, separator, token = authorization.partition(" ")
    if (
        not separator
        or scheme.casefold() != "bearer"
        or not token.strip()
    ):
        raise HTTPException(
            status_code=401,
            detail="A valid Bearer API token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        authenticated = request.app.state.api_tokens.authenticate(
            token.strip(),
            required_scope=scope,
            ip_address=client_ip(request),
        )
    except ApiTokenError as exc:
        missing_scope = "does not permit" in str(exc)
        raise HTTPException(
            status_code=403 if missing_scope else 401,
            detail=str(exc),
            headers=(None if missing_scope else {"WWW-Authenticate": "Bearer"}),
        ) from exc
    if not authenticated.user.has_permission(permission):
        raise HTTPException(
            status_code=403,
            detail="The token owner no longer permits this action.",
        )
    request.state.current_user = authenticated.user
    request.state.api_token = authenticated.record
    return authenticated


def start_user_session(request: Request, user: UserAccount) -> None:
    """Rotate the signed browser session after successful authentication."""
    request.session.clear()
    request.session["user_id"] = user.user_id
    request.session["session_id"] = uuid4().hex
    request.session["csrf_token"] = secrets.token_urlsafe(32)


def client_ip(request: Request) -> str | None:
    """Return a concise direct client address for authentication audit."""
    return request.client.host if request.client else None


def required_permissions(
    method: str,
    path: str,
) -> tuple[Permission, ...]:
    """Map every protected product surface to server-side capabilities."""
    normalized_method = method.upper()
    mutation = normalized_method in {"POST", "PUT", "PATCH", "DELETE"}

    task_operation_pages = {
        "/app/operations/business-rules",
        "/app/operations/pipelines",
        "/app/operations/data-integrations",
        "/app/operations/data-maps",
        "/app/operations/data-import",
        "/app/operations/metadata-import",
        "/app/operations/cube-refresh",
    }
    task_operation_runs = {
        "/api/operations/business-rules/runs",
        "/api/operations/pipelines/runs",
        "/api/operations/data-integrations/runs",
        "/api/operations/data-maps/runs",
        "/api/operations/data-import/runs",
        "/api/operations/metadata-import/runs",
        "/api/operations/cube-refresh/runs",
    }

    if path.startswith("/api/v1/planning-cycles") and mutation:
        return (Permission.PROCESS_DESIGN,)
    if path.startswith("/api/v1/access-control"):
        return (Permission.USER_MANAGE,)
    if path == "/api/operations/oracle-catalog/sync" and mutation:
        return (Permission.CATALOG_MANAGE,)
    if path in {
        "/api/operations/pipelines/register",
        "/api/operations/data-integrations/register",
    }:
        return (Permission.CATALOG_MANAGE,)
    if path.startswith("/api/v1/jobs"):
        return (Permission.HISTORY_VIEW,)
    if path == "/api/v1/operations":
        return (Permission.OPERATION_EXECUTE,)

    if path == "/app/agent" or path.startswith("/api/agent"):
        return (Permission.AGENT_USE,)
    if path.startswith("/app/access-control") or path.startswith(
        "/api/access/users"
    ):
        return (Permission.USER_MANAGE,)
    if path.startswith("/app/process-designer") or path.startswith(
        "/api/process-designer"
    ):
        return (Permission.PROCESS_DESIGN,)
    if path.startswith("/app/schedules") or path.startswith("/api/schedules"):
        return (Permission.SCHEDULE_MANAGE,)
    if path.startswith("/app/history"):
        return (Permission.HISTORY_VIEW,)
    if path.startswith("/app/reports/"):
        return (Permission.HISTORY_VIEW, Permission.REPORT_GENERATE)
    if path == "/app/reports" or path.startswith("/api/reports"):
        return (Permission.REPORT_GENERATE,)
    if path.startswith("/api/operations/reports"):
        return (Permission.REPORT_GENERATE,)
    if path == "/app/data-review" or path.startswith("/api/data-review"):
        return (Permission.DATA_REVIEW,)
    if "substitution-variables" in path:
        return (Permission.VARIABLE_UPDATE,)
    if "user-variables" in path:
        return (Permission.USER_VARIABLE_UPDATE,)
    if path.startswith("/app/runs/") or path.startswith("/api/runs/"):
        return (Permission.HISTORY_VIEW, Permission.PROCESS_RUN)
    if path.startswith("/app/processes/"):
        return (Permission.PROCESS_RUN,)
    if path.startswith("/api/processes/"):
        return (Permission.PROCESS_RUN,)
    if path == "/api/uploads" and mutation:
        return (Permission.PROCESS_RUN, Permission.OPERATION_EXECUTE)
    if path.startswith("/app/operations/runs/") or path.startswith(
        "/api/operations/runs/"
    ):
        return (
            Permission.HISTORY_VIEW,
            Permission.OPERATION_EXECUTE,
            Permission.PROCESS_RUN,
        )
    if path in task_operation_pages or path in task_operation_runs:
        return (Permission.OPERATION_EXECUTE, Permission.PROCESS_RUN)
    if (
        path == "/api/operations/catalog"
        or path == "/api/operations/business-rules/catalog"
        or path == "/api/operations/data-integrations/catalog"
        or path == "/api/operations/data-import/catalog"
        or path == "/api/operations/files/catalog"
        or path == "/api/operations/metadata-import/catalog"
        or path == "/api/operations/pipelines/catalog"
        or path == "/api/operations/cube-refresh/catalog"
        or path == "/api/operations/data-maps/catalog"
        or (
            path.startswith("/api/operations/pipelines/")
            and path.endswith("/preflight")
        )
    ):
        return (Permission.OPERATION_EXECUTE, Permission.PROCESS_RUN)
    if path.startswith("/app/operations") or path.startswith(
        "/api/operations"
    ):
        return (Permission.OPERATION_EXECUTE,)
    if path == "/app/control-panel" or path.startswith("/api/control-panel"):
        return (Permission.PROCESS_RUN,)
    return ()

"""FastAPI application factory for the enterprise control center."""

from __future__ import annotations

import logging
import os
import secrets
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlencode

from authlib.integrations.base_client.errors import OAuthError

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import __version__
from app.agent.capabilities import AgentCapabilityGateway
from app.agent.models import AgentToolActivity
from app.agent.preflight import AgentActionPreflightService
from app.agent.service import AgentApplicationService
from app.api.v1 import router as v1_router
from app.application.connection import VerifyConnection
from app.application.automation_schedule_manager import (
    AutomationScheduleManager,
)
from app.application.automation_schedule_targets import (
    AutomationScheduleCoordinator,
    PipelineScheduleTargetAdapter,
    RTPRegistrySyncScheduleTargetAdapter,
)
from app.application.automation_scheduling import (
    AutomationScheduleApplicationService,
)
from app.application.data_review import DataReviewWorkspaceService
from app.application.control_center import ControlCenterService
from app.application.execution_manager import (
    PlanningProcessExecutionManager,
)
from app.application.execution_worker import DurableExecutionWorker
from app.application.execution_evidence import aggregate_record_statistics
from app.application.identity_access import IdentityAccessApplicationService
from app.identity.oracle_epm_provider import OracleEPMIdentityProvider
from app.identity.oracle_oidc import OracleOIDCClient
from app.application.operation_execution_manager import (
    OperationExecutionManager,
)
from app.application.oracle_files import (
    OracleFileCatalogApplicationService,
    OracleFilePurpose,
)
from app.application.operations import BusinessRuleOperationInput, OperationCatalogService
from app.application.planning_process import (
    PlanningProcessApplicationService,
)
from app.application.planning_work import PlanningWorkApplicationService
from app.application.process_designer import (
    ProcessDesignerApplicationService,
)
from app.application.substitution_variables import (
    SubstitutionVariableApplicationService,
)
from app.application.standalone_flow_recovery import (
    StandaloneFlowRecoveryService,
)
from app.application.user_variables import UserVariableApplicationService
from app.application.reports import ReportWorkspaceService
from app.config.settings import PROJECT_ROOT, Settings
from app.infrastructure.database.migration import assert_schema_current
from app.infrastructure.database.engine import database_for
from app.models.access_control import (
    ExecutionActor,
    Permission,
    RoleDefinition,
    TriggerSource,
    UserAccount,
)
from app.models.api_token import ApiTokenScope
from app.models.automation_schedule import (
    AutomationSchedule,
    AutomationScheduleRunStatus,
)
from app.models.oracle_artifact import (
    OracleArtifactStatus,
    OracleArtifactType,
    OracleEnvironment,
)
from app.models.workflow import WorkflowStepStatus
from app.services.access_control_service import AccessControlService
from app.services.api_token_service import ApiTokenService
from app.services.business_rule_rtp_registry import BusinessRuleRTPRegistryService
from app.services.data_validation_excel_renderer import (
    DataValidationExcelRenderer,
)
from app.services.excel_report_renderer import ExcelFormReportRenderer
from app.services.federated_authentication_service import (
    FederatedAuthenticationService,
)
from app.services.environment_configuration_service import (
    EnvironmentConfigurationService,
)
from app.services.notification_service import create_notification_service
from app.services.oracle_password_authentication_service import (
    OraclePasswordAuthenticationService,
)
from app.utils.exceptions import (
    AgentError,
    AccessControlError,
    ConfigurationError,
    EPMError,
    FederatedAuthenticationError,
)
from app.web.schemas import (
    AgentActionDraftInputsRequest,
    AgentApprovalDecisionRequest,
    AgentArtifactCatalogRecoveryRequest,
    AgentArtifactRegistrationRequest,
    AgentClarificationResponseRequest,
    AgentInputResponseRequest,
    AgentMessageRequest,
    BootstrapAdministratorRequest,
    BusinessRuleRunRequest,
    CubeRefreshRunRequest,
    DataImportRunRequest,
    DataIntegrationRegistrationRequest,
    DataIntegrationRunRequest,
    DataMapRunRequest,
    DataReviewComparisonRequest,
    DataReviewSliceRequest,
    DataReviewValidationRequest,
    ExcelPipelineRunRequest,
    MetadataImportRunRequest,
    PipelineRunRequest,
    PipelineRegistrationRequest,
    PipelineProcessDraftRequest,
    PipelineRunProfileExecutionRequest,
    PipelineRunProfileRequest,
    PlanningProcessRunRequest,
    PlatformLoginRequest,
    PlatformPasswordResetRequest,
    PlatformUserRequest,
    PlatformUserUpdateRequest,
    AutomationScheduleEnabledRequest,
    AutomationScheduleRequest,
    ReportPreflightRequest,
    ReportRegistrationRequest,
    ReportRunRequest,
    StandaloneFlowRecoveryRunRequest,
    SubstitutionVariableRunRequest,
    UserVariableRunRequest,
)
from app.web.uploads import ProcessUploadStore
from app.web.api_versioning import ApiVersioningMiddleware
from app.web.request_correlation import RequestCorrelationMiddleware
from app.web.security import (
    client_ip as _client_ip,
    csrf_token as _csrf_token,
    current_user as _current_user,
    require_api_session,
    require_bearer_token,
    require_page_session as require_session,
    start_user_session as _start_user_session,
    validate_csrf as _validate_csrf,
)

WEB_ROOT = Path(__file__).resolve().parent
LOGGER = logging.getLogger("oracle_planning_automation.web")


def create_app(
    settings: Settings | None = None,
    *,
    session_secret: str | None = None,
    connection_use_case_factory: (
        Callable[[Settings], VerifyConnection] | None
    ) = None,
) -> FastAPI:
    """Create an isolated, testable FastAPI application."""
    initial_settings = settings or Settings.from_env()
    assert_schema_current(
        initial_settings.database_target,
        project_root=PROJECT_ROOT,
    )
    environment_configuration = EnvironmentConfigurationService(
        initial_settings,
        logger=LOGGER.getChild("environment_configuration"),
    )
    resolved_settings = environment_configuration.resolve_startup_settings()
    resolved_secret = (
        session_secret
        or os.getenv("WEB_SESSION_SECRET", "").strip()
        or secrets.token_urlsafe(32)
    )
    if not session_secret and not os.getenv("WEB_SESSION_SECRET"):
        LOGGER.warning(
            "WEB_SESSION_SECRET is not configured; web sessions will reset "
            "when the server restarts."
        )

    execution_manager = PlanningProcessExecutionManager(
        resolved_settings,
        logger=LOGGER.getChild("execution_manager"),
    )
    operation_manager = OperationExecutionManager(
        resolved_settings,
        logger=LOGGER.getChild("operation_manager"),
    )
    operation_catalog = OperationCatalogService(
        resolved_settings,
        logger=LOGGER.getChild("operation_catalog"),
    )
    automation_schedule_service = AutomationScheduleApplicationService(
        resolved_settings.database_target
    )
    automation_schedule_coordinator = AutomationScheduleCoordinator(
        automation_schedule_service,
        operation_manager,
        (
            PipelineScheduleTargetAdapter(
                resolved_settings,
                catalog=operation_catalog,
            ),
            RTPRegistrySyncScheduleTargetAdapter(resolved_settings),
        ),
        notification_service=create_notification_service(
            resolved_settings.email_notifications,
            logger=LOGGER.getChild("schedule_notifications"),
        ),
        environment_url=resolved_settings.epm_base_url,
        application_name=resolved_settings.application_name,
        logger=LOGGER.getChild("automation_schedule_coordinator"),
    )
    automation_schedule_manager = AutomationScheduleManager(
        automation_schedule_coordinator,
        poll_interval=resolved_settings.schedule_poll_interval,
        logger=LOGGER.getChild("automation_schedule_manager"),
    )
    flow_recovery = StandaloneFlowRecoveryService(
        resolved_settings,
        catalog=operation_catalog,
    )
    process_service = PlanningProcessApplicationService(
        resolved_settings,
        logger=LOGGER.getChild("process_service"),
    )
    process_designer = ProcessDesignerApplicationService(
        resolved_settings,
        logger=LOGGER.getChild("process_designer"),
    )
    recovery_worker = DurableExecutionWorker(
        resolved_settings,
        worker_id=f"web-recovery-{secrets.token_hex(6)}",
        logger=LOGGER.getChild("recovery"),
    )
    access_control = AccessControlService(
        resolved_settings.database_target
    )
    api_tokens = ApiTokenService(
        resolved_settings.database_target,
        access_control=access_control,
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        recovery_worker.recover_expired()
        if resolved_settings.execution_runtime == "embedded":
            automation_schedule_manager.start()
        try:
            yield
        finally:
            if resolved_settings.execution_runtime == "embedded":
                automation_schedule_manager.shutdown()
            execution_manager.shutdown()
            operation_manager.shutdown()
            application.state.agent_service.shutdown()

    app = FastAPI(
        title="BISP Solutions Oracle EPM Automation",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.platform_database = database_for(
        resolved_settings.database_target
    )
    app.state.environment_configuration = EnvironmentConfigurationService(
        resolved_settings,
        logger=LOGGER.getChild("environment_configuration"),
    )
    app.state.access_control = access_control
    app.state.federated_authentication = FederatedAuthenticationService(
        resolved_settings.database_target
    )
    app.state.oracle_password_authentication = (
        OraclePasswordAuthenticationService(resolved_settings)
        if resolved_settings.oracle_password_login_ready
        else None
    )
    app.state.oracle_oidc = (
        OracleOIDCClient(resolved_settings)
        if resolved_settings.federated_identity_ready
        else None
    )
    app.state.api_tokens = api_tokens
    app.state.connection_use_case_factory = (
        connection_use_case_factory or VerifyConnection
    )
    app.state.control_center = ControlCenterService(resolved_settings)
    app.state.process_service = process_service
    app.state.process_designer = process_designer
    app.state.automation_schedule_service = automation_schedule_service
    app.state.automation_schedule_coordinator = (
        automation_schedule_coordinator
    )
    app.state.automation_schedule_manager = automation_schedule_manager
    app.state.execution_manager = execution_manager
    app.state.operation_manager = operation_manager
    app.state.operation_catalog = operation_catalog
    app.state.business_rule_rtp_registry = BusinessRuleRTPRegistryService(
        resolved_settings
    )
    app.state.flow_recovery = flow_recovery
    app.state.oracle_file_catalog = OracleFileCatalogApplicationService(
        resolved_settings,
        logger=LOGGER.getChild("oracle_file_catalog"),
    )
    app.state.substitution_variables = (
        SubstitutionVariableApplicationService(
            resolved_settings,
            logger=LOGGER.getChild("substitution_variables"),
        )
    )
    app.state.user_variables = UserVariableApplicationService(
        resolved_settings,
        logger=LOGGER.getChild("user_variables"),
    )
    app.state.report_workspace = ReportWorkspaceService(
        resolved_settings,
        logger=LOGGER.getChild("report_workspace"),
    )
    app.state.data_review = DataReviewWorkspaceService(
        resolved_settings,
        logger=LOGGER.getChild("data_review"),
    )
    app.state.identity_access = IdentityAccessApplicationService(
        resolved_settings
    )
    app.state.planning_work = PlanningWorkApplicationService(
        resolved_settings
    )
    app.state.agent_service = AgentApplicationService(
        resolved_settings,
        gateway=AgentCapabilityGateway(
            resolved_settings,
            control_center=app.state.control_center,
            data_review=app.state.data_review,
            operation_catalog=app.state.operation_catalog,
            user_variables=app.state.user_variables,
            business_rule_rtps=app.state.business_rule_rtp_registry,
            schedule_service=app.state.automation_schedule_service,
            schedule_coordinator=app.state.automation_schedule_coordinator,
        ),
        preflight=AgentActionPreflightService(
            control_center=app.state.control_center,
            operation_catalog=app.state.operation_catalog,
            report_workspace=app.state.report_workspace,
        ),
        operation_manager=app.state.operation_manager,
        schedule_service=app.state.automation_schedule_service,
        schedule_coordinator=app.state.automation_schedule_coordinator,
        logger=LOGGER.getChild("agent"),
    )
    app.state.upload_store = ProcessUploadStore(
        resolved_settings.runtime_storage_dir / "web_uploads"
    )
    app.add_middleware(
        SessionMiddleware,
        secret_key=resolved_secret,
        session_cookie="bisp_epm_session",
        same_site="lax",
        https_only=_secure_cookie_enabled(),
        max_age=8 * 60 * 60,
    )
    app.add_middleware(ApiVersioningMiddleware)
    app.add_middleware(
        RequestCorrelationMiddleware,
        logger=LOGGER.getChild("http"),
    )
    app.mount(
        "/static",
        StaticFiles(directory=WEB_ROOT / "static"),
        name="static",
    )
    app.include_router(v1_router)
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; "
            "style-src 'self'; script-src 'self'; "
            "font-src 'self'; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health/live", include_in_schema=False)
    async def liveness_probe():
        """Confirm that the API process can serve HTTP requests."""
        return {
            "status": "alive",
            "service": "bisp-epm-api",
            "version": __version__,
        }

    @app.get("/health/ready", include_in_schema=False)
    async def readiness_probe(request: Request):
        """Confirm that required platform persistence is reachable."""
        def verify_database() -> None:
            with request.app.state.platform_database.connect() as connection:
                connection.execute(text("SELECT 1"))

        try:
            await run_in_threadpool(verify_database)
        except SQLAlchemyError:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "unavailable",
                    "service": "bisp-epm-api",
                    "version": __version__,
                    "components": {"database": "unavailable"},
                },
            )
        return {
            "status": "ready",
            "service": "bisp-epm-api",
            "version": __version__,
            "components": {"database": "available"},
            "environment_configured": bool(
                request.app.state.settings.application_name
            ),
        }

    def modern_workspace_redirect(
        request: Request,
        view: str,
        **parameters: str | int | None,
    ) -> Response:
        """Route all browser entry points to the current React application."""
        frontend_url = resolved_settings.web_frontend_url
        query = dict(request.query_params)
        query.update(
            {
                name: str(value)
                for name, value in parameters.items()
                if value is not None and str(value).strip()
            }
        )
        query_string = f"?{urlencode(query)}" if query else ""
        if frontend_url is None:
            frontend_index = PROJECT_ROOT / "frontend" / "dist" / "index.html"
            if view == "home" and frontend_index.is_file():
                return FileResponse(frontend_index)
            if not frontend_index.is_file():
                return JSONResponse(
                    status_code=503,
                    content={
                        "status": "unavailable",
                        "message": "The React frontend has not been built.",
                        "details": (
                            "Run 'pnpm build' in the frontend directory or "
                            "configure WEB_FRONTEND_URL."
                        ),
                    },
                )
            return RedirectResponse(
                url=f"/{query_string}#{view}",
                status_code=303,
            )
        return RedirectResponse(
            url=f"{frontend_url}/{query_string}#{view}",
            status_code=303,
        )

    def identity_redirect(*, error: str | None = None) -> RedirectResponse:
        """Return only fixed same-product destinations after OIDC callbacks."""
        if resolved_settings.web_frontend_url:
            target = f"{resolved_settings.web_frontend_url}/"
            if error:
                target = f"{target}?{urlencode({'auth_error': error})}"
        else:
            target = "/" if error is None else f"/?auth_error={quote(error)}"
        return RedirectResponse(target, status_code=303)

    @app.get("/auth/oracle/start", include_in_schema=False)
    async def oracle_identity_start(request: Request):
        """Redirect the browser to Oracle IAM using state, nonce, and PKCE."""
        oidc = request.app.state.oracle_oidc
        if oidc is None:
            return identity_redirect(error="federated_not_configured")
        redirect_uri = (
            resolved_settings.oracle_identity_redirect_uri
            or str(request.url_for("oracle_identity_callback"))
        )
        try:
            return await oidc.begin(request, redirect_uri)
        except Exception:
            LOGGER.exception("Oracle OIDC authorization could not be started.")
            return identity_redirect(error="identity_provider_unavailable")

    @app.get("/auth/oracle/callback", include_in_schema=False)
    async def oracle_identity_callback(request: Request):
        """Validate Oracle tokens and start a session for an approved linked user."""
        oidc = request.app.state.oracle_oidc
        if oidc is None:
            return identity_redirect(error="federated_not_configured")
        try:
            claims = await oidc.complete(request)
            provider_code = OracleEPMIdentityProvider.definition_for(
                resolved_settings
            ).code
            user = await run_in_threadpool(
                request.app.state.federated_authentication.authenticate,
                provider_code,
                subject=claims.subject,
                username=claims.username,
                ip_address=_client_ip(request),
            )
        except FederatedAuthenticationError:
            LOGGER.warning("Validated Oracle identity has no approved account.")
            return identity_redirect(error="account_not_ready")
        except OAuthError:
            LOGGER.warning("Oracle OIDC callback validation failed.", exc_info=True)
            return identity_redirect(error="identity_validation_failed")
        except Exception:
            LOGGER.exception("Oracle OIDC callback failed unexpectedly.")
            return identity_redirect(error="identity_provider_unavailable")
        _start_user_session(request, user)
        request.session["authentication_method"] = "oracle_oidc"
        return identity_redirect()

    @app.get("/", include_in_schema=False)
    async def root(request: Request):
        return modern_workspace_redirect(request, "home")

    @app.get("/setup", include_in_schema=False)
    async def setup_page(request: Request):
        return modern_workspace_redirect(request, "home")

    @app.post("/api/access/bootstrap")
    async def bootstrap_administrator(
        request: Request,
        payload: BootstrapAdministratorRequest,
    ):
        _validate_csrf(request)
        try:
            user = request.app.state.access_control.bootstrap_administrator(
                username=payload.username,
                display_name=payload.display_name,
                email=payload.email,
                password=payload.password,
                ip_address=_client_ip(request),
            )
        except AccessControlError as exc:
            return _access_error(str(exc), status_code=400)
        _start_user_session(request, user)
        return {
            "status": "success",
            "message": "Platform Administrator created.",
            "redirect": "/app",
        }

    @app.get("/login", include_in_schema=False)
    async def login_page(request: Request):
        return modern_workspace_redirect(request, "home")

    @app.post("/api/session/connect")
    async def connect(request: Request, payload: PlatformLoginRequest):
        _validate_csrf(request)
        user = await run_in_threadpool(
            request.app.state.access_control.authenticate,
            payload.username,
            payload.password,
            ip_address=_client_ip(request),
        )
        if user is None:
            return _access_error(
                "The username or password is incorrect.",
                status_code=401,
            )
        _start_user_session(request, user)
        return {
            "status": "success",
            "message": "Signed in successfully.",
            "redirect": "/app",
        }

    @app.post("/logout", include_in_schema=False)
    async def logout(request: Request):
        _validate_csrf(request)
        user = _current_user(request)
        owner = str(request.session.get("session_id", "")).strip()
        if owner:
            request.app.state.upload_store.delete_owner(owner)
        if user is not None:
            request.app.state.access_control.record_logout(
                user,
                ip_address=_client_ip(request),
            )
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/api/health")
    async def health(request: Request):
        require_api_session(request)
        factory = request.app.state.connection_use_case_factory
        health_settings = await run_in_threadpool(
            request.app.state.environment_configuration.resolve_startup_settings
        )
        active_application = request.app.state.settings.application_name
        restart_required = bool(
            health_settings.application_name
            and health_settings.application_name.casefold()
            != active_application.casefold()
        )
        try:
            result = await run_in_threadpool(
                factory(health_settings).execute
            )
        except EPMError as exc:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "unavailable",
                    "product": "Oracle EPM Automation Platform",
                    "application": health_settings.application_name,
                    "active_application": active_application,
                    "restart_required": restart_required,
                    "details": str(exc),
                },
            )
        return {
            "status": "ok",
            "product": "Oracle EPM Automation Platform",
            "application": result.application_name,
            "active_application": active_application,
            "restart_required": restart_required,
            "deployment_mode": result.deployment_mode,
            "application_type": result.application_type,
            "storage": result.storage,
            "hybrid": result.hybrid,
        }

    @app.get("/api/control-panel/snapshot")
    async def control_panel_snapshot(request: Request):
        require_api_session(request)
        snapshot = await run_in_threadpool(
            request.app.state.control_center.snapshot,
            history_limit=8,
        )
        return {
            "status": "success",
            "snapshot": asdict(snapshot),
        }

    @app.get("/app", include_in_schema=False)
    async def dashboard(request: Request):
        return modern_workspace_redirect(request, "home")

    @app.get("/app/access-control", include_in_schema=False)
    async def access_control_page(request: Request):
        return modern_workspace_redirect(request, "access")

    @app.get("/api/access/users")
    async def list_platform_users(request: Request):
        require_api_session(request)
        return {
            "status": "success",
            "users": [
                _user_payload(user)
                for user in request.app.state.access_control.list_users()
            ],
            "roles": [
                _role_payload(role)
                for role in request.app.state.access_control.roles()
            ],
        }

    @app.post("/api/access/users")
    async def create_platform_user(
        request: Request,
        payload: PlatformUserRequest,
    ):
        require_api_session(request)
        actor = request.state.current_user
        try:
            user = request.app.state.access_control.create_user(
                username=payload.username,
                display_name=payload.display_name,
                email=payload.email,
                password=payload.password,
                roles=payload.roles,
                actor_user_id=actor.user_id,
            )
        except AccessControlError as exc:
            return _access_error(str(exc), status_code=400)
        return JSONResponse(
            status_code=201,
            content={"status": "success", "user": _user_payload(user)},
        )

    @app.put("/api/access/users/{user_id}")
    async def update_platform_user(
        request: Request,
        user_id: int,
        payload: PlatformUserUpdateRequest,
    ):
        require_api_session(request)
        actor = request.state.current_user
        if user_id == actor.user_id and not payload.active:
            return _access_error(
                "You cannot deactivate your own signed-in account.",
                status_code=400,
            )
        try:
            user = request.app.state.access_control.update_user(
                user_id,
                display_name=payload.display_name,
                email=payload.email,
                active=payload.active,
                roles=payload.roles,
                actor_user_id=actor.user_id,
            )
        except AccessControlError as exc:
            return _access_error(str(exc), status_code=400)
        return {"status": "success", "user": _user_payload(user)}

    @app.post("/api/access/users/{user_id}/password")
    async def reset_platform_password(
        request: Request,
        user_id: int,
        payload: PlatformPasswordResetRequest,
    ):
        require_api_session(request)
        actor = request.state.current_user
        try:
            request.app.state.access_control.reset_password(
                user_id,
                payload.password,
                actor_user_id=actor.user_id,
            )
        except AccessControlError as exc:
            return _access_error(str(exc), status_code=400)
        return {
            "status": "success",
            "message": "Password updated successfully.",
        }

    @app.get("/app/processes", include_in_schema=False)
    async def processes_page(request: Request):
        return modern_workspace_redirect(request, "cycles")

    @app.get("/app/process-designer", include_in_schema=False)
    async def process_designer_page(
        request: Request,
        process_code: str | None = None,
    ):
        return modern_workspace_redirect(
            request,
            "cycles",
            process_code=process_code,
        )

    @app.get("/app/control-panel", include_in_schema=False)
    async def planning_control_panel(
        request: Request,
        process_code: str | None = None,
    ):
        return modern_workspace_redirect(
            request,
            "cycles",
            process_code=process_code,
        )

    @app.get("/app/operations", include_in_schema=False)
    async def operations_page(request: Request):
        return modern_workspace_redirect(request, "operations")

    @app.get("/app/schedules", include_in_schema=False)
    async def schedules_page(request: Request):
        return modern_workspace_redirect(request, "schedules")

    @app.get("/api/schedules")
    async def list_schedules(request: Request):
        require_api_session(request)
        try:
            environment_key = _schedule_environment(request).key
            schedules = (
                request.app.state.automation_schedule_service.list_schedules(
                    environment_key=environment_key
                )
            )
        except EPMError as exc:
            return _schedule_error("Schedules could not be loaded.", exc)
        return {
            "status": "success",
            "schedules": [_schedule_payload(item) for item in schedules],
        }

    @app.post("/api/schedules/preview")
    async def preview_schedule(
        request: Request,
        payload: AutomationScheduleRequest,
    ):
        require_api_session(request)
        try:
            preview = await run_in_threadpool(
                request.app.state.automation_schedule_coordinator.preview,
                payload.to_domain(_schedule_environment(request).key),
            )
        except EPMError as exc:
            return _schedule_error("Schedule could not be validated.", exc)
        return {
            "status": "success",
            "next_run_at": preview.next_run_at.isoformat(),
            "next_run_local": preview.next_run_local.isoformat(),
            "message": (
                "Oracle can generate the Calculation Manager snapshot, and "
                "the automated recurrence is ready."
                if payload.target_type.value == "RTP_REGISTRY_SYNC"
                else "The live Oracle Pipeline definition, unattended inputs, "
                "and recurrence are ready."
            ),
        }

    @app.post("/api/schedules")
    async def create_schedule(
        request: Request,
        payload: AutomationScheduleRequest,
    ):
        require_api_session(request)
        try:
            schedule = await run_in_threadpool(
                request.app.state.automation_schedule_coordinator.create,
                payload.to_domain(_schedule_environment(request).key),
            )
        except EPMError as exc:
            return _schedule_error("Schedule could not be created.", exc)
        return JSONResponse(
            status_code=201,
            content={
                "status": "success",
                "message": f"Schedule '{schedule.name}' was created.",
                "schedule": _schedule_payload(schedule),
            },
        )

    @app.put("/api/schedules/{schedule_id}")
    async def update_schedule(
        request: Request,
        schedule_id: int,
        payload: AutomationScheduleRequest,
    ):
        require_api_session(request)
        try:
            existing = request.app.state.automation_schedule_service.get(
                schedule_id
            )
            if existing.environment_key != _schedule_environment(request).key:
                raise ConfigurationError("Schedule was not found.")
            schedule = await run_in_threadpool(
                request.app.state.automation_schedule_coordinator.update,
                schedule_id,
                payload.to_domain(_schedule_environment(request).key),
            )
        except EPMError as exc:
            return _schedule_error("Schedule could not be updated.", exc)
        return {
            "status": "success",
            "message": f"Schedule '{schedule.name}' was updated.",
            "schedule": _schedule_payload(schedule),
        }

    @app.patch("/api/schedules/{schedule_id}/enabled")
    async def set_schedule_enabled(
        request: Request,
        schedule_id: int,
        payload: AutomationScheduleEnabledRequest,
    ):
        require_api_session(request)
        try:
            existing = request.app.state.automation_schedule_service.get(
                schedule_id
            )
            if existing.environment_key != _schedule_environment(request).key:
                raise ConfigurationError("Schedule was not found.")
            schedule = await run_in_threadpool(
                request.app.state.automation_schedule_coordinator.set_enabled,
                schedule_id,
                payload.enabled,
            )
        except EPMError as exc:
            return _schedule_error("Schedule status could not be changed.", exc)
        action = "resumed" if schedule.enabled else "paused"
        return {
            "status": "success",
            "message": f"Schedule '{schedule.name}' was {action}.",
            "schedule": _schedule_payload(schedule),
        }

    @app.get("/api/schedules/{schedule_id}/runs")
    async def list_schedule_runs(request: Request, schedule_id: int):
        require_api_session(request)
        try:
            schedule = request.app.state.automation_schedule_service.get(
                schedule_id
            )
            if schedule.environment_key != _schedule_environment(request).key:
                raise ConfigurationError("Schedule was not found.")
            runs = request.app.state.automation_schedule_service.list_runs(
                schedule_id,
                limit=100,
            )
        except EPMError as exc:
            return _schedule_error("Schedule history could not be loaded.", exc)
        return {
            "status": "success",
            "runs": [_schedule_run_payload(item) for item in runs],
        }

    @app.get("/api/schedules/runs/history")
    async def list_schedule_run_evidence(
        request: Request,
        schedule_id: int | None = Query(default=None, gt=0),
        status: AutomationScheduleRunStatus | None = None,
        scheduled_from: datetime | None = None,
        scheduled_to: datetime | None = None,
        limit: int = Query(default=100, ge=1, le=1_000),
    ):
        require_api_session(request)
        try:
            evidence = (
                request.app.state.automation_schedule_service
                .list_run_evidence(
                    _schedule_environment(request).key,
                    schedule_id=schedule_id,
                    status=status,
                    scheduled_from=scheduled_from,
                    scheduled_to=scheduled_to,
                    limit=limit,
                )
            )
        except EPMError as exc:
            return _schedule_error("Schedule history could not be loaded.", exc)
        counts = {item.value: 0 for item in AutomationScheduleRunStatus}
        for item in evidence:
            counts[item.run.status.value] += 1
        return {
            "status": "success",
            "summary": {
                "total": len(evidence),
                "submitted": counts["SUBMITTED"],
                "completed": counts["COMPLETED"],
                "failed": counts["FAILED"],
                "skipped": counts["SKIPPED"],
                "claimed": counts["CLAIMED"],
            },
            "runs": [_schedule_evidence_payload(item) for item in evidence],
        }

    @app.delete("/api/schedules/{schedule_id}")
    async def delete_schedule(request: Request, schedule_id: int):
        require_api_session(request)
        try:
            schedule = request.app.state.automation_schedule_service.get(
                schedule_id
            )
            if schedule.environment_key != _schedule_environment(request).key:
                raise ConfigurationError("Schedule was not found.")
            await run_in_threadpool(
                request.app.state.automation_schedule_service.archive,
                schedule_id,
            )
        except EPMError as exc:
            return _schedule_error("Schedule could not be deleted.", exc)
        return {
            "status": "success",
            "message": "Schedule was archived. Execution history was retained.",
        }

    @app.get(
        "/app/operations/business-rules",
        include_in_schema=False,
    )
    async def business_rule_page(request: Request):
        return modern_workspace_redirect(
            request, "operations", operation="business-rules"
        )

    @app.get("/app/operations/pipelines", include_in_schema=False)
    async def pipeline_page(request: Request):
        return modern_workspace_redirect(
            request, "operations", operation="pipelines"
        )

    @app.get(
        "/app/operations/data-integrations",
        include_in_schema=False,
    )
    async def data_integration_page(request: Request):
        return modern_workspace_redirect(
            request, "operations", operation="data-integrations"
        )

    @app.get(
        "/app/operations/metadata-import",
        include_in_schema=False,
    )
    async def metadata_import_page(request: Request):
        return modern_workspace_redirect(
            request, "operations", operation="metadata-import"
        )

    @app.get(
        "/app/operations/data-import",
        include_in_schema=False,
    )
    async def data_import_page(request: Request):
        return modern_workspace_redirect(
            request, "operations", operation="data-import"
        )

    @app.get(
        "/app/operations/substitution-variables",
        include_in_schema=False,
    )
    async def substitution_variables_page(request: Request):
        return modern_workspace_redirect(
            request, "operations", operation="substitution-variables"
        )

    @app.get(
        "/app/operations/cube-refresh",
        include_in_schema=False,
    )
    async def cube_refresh_page(request: Request):
        return modern_workspace_redirect(
            request, "operations", operation="cube-refresh"
        )

    @app.get("/app/operations/data-maps", include_in_schema=False)
    async def data_map_page(request: Request):
        return modern_workspace_redirect(
            request, "operations", operation="data-maps"
        )

    @app.get("/app/processes/{process_code}", include_in_schema=False)
    async def process_setup_page(request: Request, process_code: str):
        return modern_workspace_redirect(
            request,
            "cycles",
            process_code=process_code,
        )

    @app.get("/app/runs/{execution_id}", include_in_schema=False)
    async def run_detail_page(request: Request, execution_id: str):
        return modern_workspace_redirect(
            request, "jobs", execution_id=execution_id
        )

    @app.get("/app/history", include_in_schema=False)
    async def history_page(request: Request):
        return modern_workspace_redirect(request, "jobs")

    @app.get("/app/reports", include_in_schema=False)
    async def reports_page(request: Request):
        return modern_workspace_redirect(request, "reports")

    @app.get("/app/data-review", include_in_schema=False)
    async def data_review_page(request: Request):
        return modern_workspace_redirect(request, "data-review")

    @app.get("/app/agent", include_in_schema=False)
    async def agent_workspace_page(request: Request):
        return modern_workspace_redirect(request, "assistant")

    @app.get("/api/agent/status")
    async def agent_status(request: Request):
        require_api_session(request)
        return request.app.state.agent_service.status()

    @app.get("/api/agent/conversations")
    async def agent_conversations(request: Request):
        require_api_session(request)
        user = _current_user(request)
        assert user is not None
        conversations = await run_in_threadpool(
            request.app.state.agent_service.list_conversations, user
        )
        return {
            "status": "success",
            "conversations": [asdict(item) for item in conversations],
        }

    @app.post("/api/agent/conversations")
    async def create_agent_conversation(request: Request):
        require_api_session(request)
        user = _current_user(request)
        assert user is not None
        conversation = await run_in_threadpool(
            request.app.state.agent_service.create_conversation, user
        )
        return {"status": "success", "conversation": asdict(conversation)}

    @app.get("/api/agent/conversations/{conversation_id}/messages")
    async def agent_messages(request: Request, conversation_id: str):
        require_api_session(request)
        user = _current_user(request)
        assert user is not None
        try:
            messages = await run_in_threadpool(
                request.app.state.agent_service.get_messages,
                conversation_id,
                user,
            )
            action_drafts = await run_in_threadpool(
                request.app.state.agent_service.get_action_drafts,
                conversation_id,
                user,
            )
            approval_request = await run_in_threadpool(
                request.app.state.agent_service.get_pending_approval,
                conversation_id,
                user,
            )
            clarification_request = await run_in_threadpool(
                request.app.state.agent_service.get_pending_clarification,
                conversation_id,
                user,
            )
            input_request = await run_in_threadpool(
                request.app.state.agent_service.get_pending_input,
                conversation_id,
                user,
            )
            data_review_context = await run_in_threadpool(
                request.app.state.agent_service.get_data_review_context,
                conversation_id,
                user,
            )
        except AgentError as exc:
            return _agent_error(exc)
        return {
            "status": "success",
            "messages": [asdict(item) for item in messages],
            "action_drafts": [asdict(item) for item in action_drafts],
            "approval_request": (
                asdict(approval_request) if approval_request else None
            ),
            "clarification_request": (
                asdict(clarification_request)
                if clarification_request
                else None
            ),
            "input_request": asdict(input_request) if input_request else None,
            "data_review_context": data_review_context,
        }

    @app.post("/api/agent/conversations/{conversation_id}/messages")
    async def send_agent_message(
        request: Request,
        conversation_id: str,
        payload: AgentMessageRequest,
    ):
        require_api_session(request)
        user = _current_user(request)
        assert user is not None
        try:
            result = await run_in_threadpool(
                request.app.state.agent_service.send_message,
                conversation_id=conversation_id,
                user=user,
                content=payload.content,
            )
        except AgentError as exc:
            return _agent_error(exc)
        return {
            "status": "success",
            "message": asdict(result["message"]),
            "tool_activity": [
                _agent_tool_activity_payload(item)
                for item in result["tool_activity"]
            ],
            "action_drafts": [
                asdict(item) for item in result["action_drafts"]
            ],
            "approval_request": (
                asdict(result["approval_request"])
                if result["approval_request"]
                else None
            ),
            "clarification_request": (
                asdict(result["clarification_request"])
                if result["clarification_request"]
                else None
            ),
            "input_request": (
                asdict(result["input_request"])
                if result["input_request"]
                else None
            ),
            "execution": result.get("execution"),
            "schedule": result.get("schedule"),
            "decision": (
                asdict(result["decision"])
                if result.get("decision")
                else None
            ),
        }

    @app.post(
        "/api/agent/conversations/{conversation_id}/approval"
    )
    async def resolve_agent_approval(
        request: Request,
        conversation_id: str,
        payload: AgentApprovalDecisionRequest,
    ):
        owner = require_api_session(request)
        user = _current_user(request)
        assert user is not None
        upload_tokens: tuple[str, ...] = ()
        operation_uploads = {}
        pending = request.app.state.agent_service.get_pending_approval(
            conversation_id,
            user,
        )
        if pending is not None:
            operation_code = pending.operation_code.casefold()
            raw_uploads: dict[str, object] = {}
            upload_label = "operation"
            if operation_code == "pipelines":
                candidate = pending.input_values.get("uploads", {})
                if isinstance(candidate, dict):
                    raw_uploads = candidate
                upload_label = "Pipeline"
            elif operation_code == "data-integrations":
                token = str(
                    pending.input_values.get("upload_token") or ""
                ).strip()
                if token:
                    raw_uploads = {"source_file": token}
                upload_label = "Data Integration"
            elif operation_code == "data-import":
                token = str(
                    pending.input_values.get("upload_token") or ""
                ).strip()
                if token:
                    raw_uploads = {"source_file": token}
                upload_label = "Planning Data Import"
            elif operation_code == "metadata-import":
                token = str(
                    pending.input_values.get("upload_token") or ""
                ).strip()
                if token:
                    raw_uploads = {"source_file": token}
                upload_label = "Metadata Import"
            elif operation_code == "standalone-flow":
                steps = pending.input_values.get("steps", [])
                if isinstance(steps, list):
                    for index, step in enumerate(steps, start=1):
                        if not isinstance(step, dict):
                            continue
                        values = step.get("input_values", {})
                        if not isinstance(values, dict):
                            continue
                        token = str(values.get("upload_token") or "").strip()
                        if token:
                            raw_uploads[
                                f"step_{index}:source_file"
                            ] = token
                        pipeline_uploads = values.get("uploads", {})
                        if isinstance(pipeline_uploads, dict):
                            for key, pipeline_token in pipeline_uploads.items():
                                normalized_token = str(
                                    pipeline_token or ""
                                ).strip()
                                if normalized_token:
                                    raw_uploads[
                                        f"step_{index}:{key}"
                                    ] = normalized_token
                upload_label = "Standalone Planning Flow"
            if raw_uploads:
                upload_tokens = tuple(
                    str(token) for token in raw_uploads.values()
                )
                if payload.decision == "approve":
                    try:
                        operation_uploads = {
                            str(key): request.app.state.upload_store.resolve(
                                str(token),
                                owner=owner,
                            )
                            for key, token in raw_uploads.items()
                        }
                    except ConfigurationError as exc:
                        return JSONResponse(
                            status_code=400,
                            content={
                                "status": "error",
                                "message": (
                                    f"A reviewed {upload_label} upload is "
                                    "unavailable."
                                ),
                                "details": str(exc),
                            },
                        )
        try:
            result = await run_in_threadpool(
                request.app.state.agent_service.resolve_approval,
                conversation_id=conversation_id,
                user=user,
                request_id=payload.request_id,
                decision=payload.decision,
                operation_uploads=operation_uploads,
                operation_cleanup=(
                    (
                        lambda: request.app.state.upload_store.delete_many(
                            upload_tokens
                        )
                    )
                    if upload_tokens and payload.decision == "approve"
                    else None
                ),
            )
        except AgentError as exc:
            if upload_tokens:
                request.app.state.upload_store.delete_many(upload_tokens)
            return _agent_error(exc)
        if upload_tokens and payload.decision == "reject":
            request.app.state.upload_store.delete_many(upload_tokens)
        return {
            "status": "success",
            "message": asdict(result["message"]),
            "tool_activity": [
                _agent_tool_activity_payload(item)
                for item in result["tool_activity"]
            ],
            "action_drafts": [
                asdict(item) for item in result["action_drafts"]
            ],
            "approval_request": (
                asdict(result["approval_request"])
                if result["approval_request"]
                else None
            ),
            "clarification_request": (
                asdict(result["clarification_request"])
                if result["clarification_request"]
                else None
            ),
            "input_request": (
                asdict(result["input_request"])
                if result["input_request"]
                else None
            ),
            "execution": result.get("execution"),
            "schedule": result.get("schedule"),
            "decision": (
                asdict(result["decision"])
                if result.get("decision")
                else None
            ),
        }

    @app.post(
        "/api/agent/conversations/{conversation_id}/clarification"
    )
    async def resolve_agent_clarification(
        request: Request,
        conversation_id: str,
        payload: AgentClarificationResponseRequest,
    ):
        require_api_session(request)
        user = _current_user(request)
        assert user is not None
        try:
            result = await run_in_threadpool(
                request.app.state.agent_service.resolve_clarification,
                conversation_id=conversation_id,
                user=user,
                request_id=payload.request_id,
                value=payload.value,
            )
        except AgentError as exc:
            return _agent_error(exc)
        return {
            "status": "success",
            "message": asdict(result["message"]),
            "tool_activity": [
                _agent_tool_activity_payload(item)
                for item in result["tool_activity"]
            ],
            "action_drafts": [
                asdict(item) for item in result["action_drafts"]
            ],
            "approval_request": (
                asdict(result["approval_request"])
                if result["approval_request"]
                else None
            ),
            "clarification_request": (
                asdict(result["clarification_request"])
                if result["clarification_request"]
                else None
            ),
            "input_request": (
                asdict(result["input_request"])
                if result["input_request"]
                else None
            ),
        }

    @app.post(
        "/api/agent/conversations/{conversation_id}/artifacts/synchronize"
    )
    async def synchronize_agent_artifact_catalog(
        request: Request,
        conversation_id: str,
        payload: AgentArtifactCatalogRecoveryRequest,
    ):
        """Refresh safe Oracle discovery for one pending agent choice."""
        require_api_session(request)
        user = _current_user(request)
        assert user is not None
        try:
            clarification = await run_in_threadpool(
                request.app.state.agent_service.synchronize_clarification_catalog,
                conversation_id=conversation_id,
                user=user,
                request_id=payload.request_id,
            )
        except AgentError as exc:
            return _agent_error(exc)
        return {
            "status": "success",
            "message": (
                "The safely discoverable Oracle catalog was synchronized."
            ),
            "clarification_request": asdict(clarification),
        }

    @app.post(
        "/api/agent/conversations/{conversation_id}/artifacts/register"
    )
    async def register_agent_artifact(
        request: Request,
        conversation_id: str,
        payload: AgentArtifactRegistrationRequest,
    ):
        """Register an exact artifact and continue the governed agent flow."""
        require_api_session(request)
        user = _current_user(request)
        assert user is not None
        try:
            result = await run_in_threadpool(
                request.app.state.agent_service.register_clarification_artifact,
                conversation_id=conversation_id,
                user=user,
                request_id=payload.request_id,
                identifier=payload.identifier,
            )
        except AgentError as exc:
            return _agent_error(exc)
        return {
            "status": "success",
            "message": asdict(result["message"]),
            "tool_activity": [
                _agent_tool_activity_payload(item)
                for item in result["tool_activity"]
            ],
            "action_drafts": [
                asdict(item) for item in result["action_drafts"]
            ],
            "approval_request": (
                asdict(result["approval_request"])
                if result["approval_request"]
                else None
            ),
            "clarification_request": (
                asdict(result["clarification_request"])
                if result["clarification_request"]
                else None
            ),
            "input_request": (
                asdict(result["input_request"])
                if result["input_request"]
                else None
            ),
            "execution": result.get("execution"),
        }

    @app.post(
        "/api/agent/conversations/{conversation_id}/inputs"
    )
    async def resolve_agent_input(
        request: Request,
        conversation_id: str,
        payload: AgentInputResponseRequest,
    ):
        require_api_session(request)
        user = _current_user(request)
        assert user is not None
        try:
            result = await run_in_threadpool(
                request.app.state.agent_service.resolve_input,
                conversation_id=conversation_id,
                user=user,
                request_id=payload.request_id,
                values=payload.values,
            )
        except AgentError as exc:
            return _agent_error(exc)
        return {
            "status": "success",
            "message": asdict(result["message"]),
            "tool_activity": [
                _agent_tool_activity_payload(item)
                for item in result["tool_activity"]
            ],
            "action_drafts": [
                asdict(item) for item in result["action_drafts"]
            ],
            "approval_request": (
                asdict(result["approval_request"])
                if result["approval_request"]
                else None
            ),
            "clarification_request": (
                asdict(result["clarification_request"])
                if result["clarification_request"]
                else None
            ),
            "input_request": (
                asdict(result["input_request"])
                if result["input_request"]
                else None
            ),
        }

    @app.delete("/api/agent/conversations/{conversation_id}")
    async def delete_agent_conversation(
        request: Request,
        conversation_id: str,
    ):
        require_api_session(request)
        user = _current_user(request)
        assert user is not None
        try:
            await run_in_threadpool(
                request.app.state.agent_service.delete_conversation,
                conversation_id,
                user,
            )
        except AgentError as exc:
            return _agent_error(exc)
        return {"status": "success"}

    @app.post("/api/agent/action-drafts/{draft_id}/preflight")
    async def preflight_agent_action_draft(
        request: Request,
        draft_id: str,
    ):
        require_api_session(request)
        user = _current_user(request)
        assert user is not None
        try:
            draft = await run_in_threadpool(
                request.app.state.agent_service.preflight_action_draft,
                draft_id,
                user,
            )
        except AgentError as exc:
            return _agent_error(exc)
        return {
            "status": "success",
            "action_draft": asdict(draft),
        }

    @app.get("/api/agent/action-drafts/{draft_id}/handoff")
    async def agent_operation_handoff(
        request: Request,
        draft_id: str,
        target_code: str,
    ):
        require_api_session(request)
        user = _current_user(request)
        assert user is not None
        try:
            draft = await run_in_threadpool(
                request.app.state.agent_service.resolve_operation_handoff,
                draft_id=draft_id,
                user=user,
                target_code=target_code,
            )
        except AgentError as exc:
            return _agent_error(exc)
        return {"status": "success", "action_draft": asdict(draft)}

    @app.patch("/api/agent/action-drafts/{draft_id}/inputs")
    async def update_agent_action_draft_inputs(
        request: Request,
        draft_id: str,
        payload: AgentActionDraftInputsRequest,
    ):
        require_api_session(request)
        user = _current_user(request)
        assert user is not None
        try:
            draft = await run_in_threadpool(
                request.app.state.agent_service.update_action_draft_inputs,
                draft_id,
                user,
                payload.inputs,
            )
        except AgentError as exc:
            return _agent_error(exc)
        return {
            "status": "success",
            "action_draft": asdict(draft),
        }

    @app.get("/api/data-review/cubes")
    async def data_review_cubes(request: Request):
        require_api_session(request)
        try:
            cubes = await run_in_threadpool(
                request.app.state.data_review.list_cubes
            )
        except EPMError as exc:
            return _data_review_error("Cubes could not be loaded.", exc)
        return {
            "status": "success",
            "cubes": [asdict(item) for item in cubes],
        }

    @app.get("/api/data-review/cubes/{cube}/dimensions")
    async def data_review_dimensions(request: Request, cube: str):
        require_api_session(request)
        try:
            dimensions = await run_in_threadpool(
                request.app.state.data_review.list_dimensions,
                cube,
            )
        except EPMError as exc:
            return _data_review_error(
                "Cube dimensions could not be loaded.",
                exc,
            )
        return {
            "status": "success",
            "cube": cube,
            "dimensions": [asdict(item) for item in dimensions],
        }

    @app.get(
        "/api/data-review/cubes/{cube}/dimensions/{dimension}/members"
    )
    async def data_review_members(
        request: Request,
        cube: str,
        dimension: str,
        q: str = "",
        offset: int = 0,
        limit: int = 40,
    ):
        require_api_session(request)
        try:
            result = await run_in_threadpool(
                request.app.state.data_review.search_members,
                cube,
                dimension,
                query=q,
                offset=offset,
                limit=limit,
            )
        except EPMError as exc:
            return _data_review_error(
                "Dimension members could not be loaded.",
                exc,
            )
        return {"status": "success", **asdict(result)}

    @app.post("/api/data-review/grid")
    async def data_review_grid(
        request: Request,
        payload: DataReviewSliceRequest,
    ):
        require_api_session(request)
        try:
            grid = await run_in_threadpool(
                request.app.state.data_review.load_slice,
                payload.to_domain(),
            )
        except EPMError as exc:
            return _data_review_error("Cube data could not be loaded.", exc)
        return {"status": "success", "review": asdict(grid)}

    @app.post("/api/data-review/grid/export")
    async def export_data_review_grid(
        request: Request,
        payload: DataReviewSliceRequest,
    ):
        require_api_session(request)
        try:
            grid = await run_in_threadpool(
                request.app.state.data_review.load_slice,
                payload.to_domain(),
            )
            content = await run_in_threadpool(
                ExcelFormReportRenderer().render_bytes,
                application_name=resolved_settings.application_name,
                form_name=grid.form_name,
                title=f"{grid.cube} Data Review",
                grid=grid.grid,
                generated_at=datetime.now().astimezone(),
            )
        except EPMError as exc:
            return _data_review_error("Data Review export failed.", exc)
        return _excel_response(content, f"{grid.cube}-data-review.xlsx")

    @app.post("/api/data-review/validate")
    async def validate_data_review(
        request: Request,
        payload: DataReviewValidationRequest,
    ):
        require_api_session(request)
        try:
            validation = await run_in_threadpool(
                request.app.state.data_review.validate_slice,
                payload.slice.to_domain(),
                payload.rules.to_domain(),
            )
            evidence = None
            if payload.planning_task_id is not None:
                actor = _current_user(request)
                assert actor is not None
                evidence = request.app.state.planning_work.record_quality_validation(
                    payload.planning_task_id,
                    selection=payload.slice.model_dump(mode="json"),
                    criteria=payload.rules.model_dump(mode="json"),
                    result=validation.result,
                    actor=actor,
                )
        except EPMError as exc:
            return _data_review_error("Data validation failed.", exc)
        return {
            "status": "success",
            "validation": asdict(validation),
            "task_validation": (
                _planning_validation_payload(evidence) if evidence else None
            ),
        }

    @app.post("/api/data-review/validate/export")
    async def export_data_review_validation(
        request: Request,
        payload: DataReviewValidationRequest,
    ):
        require_api_session(request)
        selection = payload.slice.to_domain()
        try:
            validation = await run_in_threadpool(
                request.app.state.data_review.validate_slice,
                selection,
                payload.rules.to_domain(),
            )
            content = await run_in_threadpool(
                DataValidationExcelRenderer().render_quality,
                application_name=resolved_settings.application_name,
                cube=validation.cube,
                pov=selection.pov,
                result=validation.result,
            )
        except EPMError as exc:
            return _data_review_error("Validation export failed.", exc)
        return _excel_response(content, "data-quality-validation.xlsx")

    @app.post("/api/data-review/compare")
    async def compare_data_review_forms(
        request: Request,
        payload: DataReviewComparisonRequest,
    ):
        require_api_session(request)
        try:
            comparison = await run_in_threadpool(
                request.app.state.data_review.compare_slices,
                payload.source.to_domain(),
                payload.target.to_domain(),
                tolerance=payload.tolerance,
                max_mismatches=payload.max_mismatches,
                include_cells=payload.include_cells,
            )
            evidence = None
            if payload.planning_task_id is not None:
                actor = _current_user(request)
                assert actor is not None
                evidence = request.app.state.planning_work.record_comparison_validation(
                    payload.planning_task_id,
                    selection=payload.source.model_dump(mode="json"),
                    target_cube=comparison.target_cube,
                    criteria={
                        "tolerance": str(payload.tolerance),
                        "max_mismatches": payload.max_mismatches,
                    },
                    result=comparison.result,
                    actor=actor,
                )
        except EPMError as exc:
            return _data_review_error("Data comparison failed.", exc)
        return {
            "status": "success",
            "comparison": asdict(comparison),
            "task_validation": (
                _planning_validation_payload(evidence) if evidence else None
            ),
        }

    @app.post("/api/data-review/compare/export")
    async def export_data_review_comparison(
        request: Request,
        payload: DataReviewComparisonRequest,
    ):
        require_api_session(request)
        source = payload.source.to_domain()
        target = payload.target.to_domain()
        try:
            comparison = await run_in_threadpool(
                request.app.state.data_review.compare_slices,
                source,
                target,
                tolerance=payload.tolerance,
                max_mismatches=payload.max_mismatches,
                include_cells=payload.include_cells,
            )
            content = await run_in_threadpool(
                DataValidationExcelRenderer().render_comparison,
                application_name=resolved_settings.application_name,
                source_cube=comparison.source_cube,
                target_cube=comparison.target_cube,
                source_pov=source.pov,
                target_pov=target.pov,
                result=comparison.result,
            )
        except EPMError as exc:
            return _data_review_error("Comparison export failed.", exc)
        return _excel_response(content, "source-target-validation.xlsx")

    @app.get(
        "/app/reports/{filename}",
        include_in_schema=False,
        response_class=FileResponse,
    )
    async def download_report(request: Request, filename: str):
        redirect = require_session(request)
        if redirect is not None:
            return redirect
        directory = resolved_settings.report_output_dir.resolve()
        candidate = (directory / filename).resolve()
        if (
            candidate.parent != directory
            or candidate.suffix.casefold() != ".xlsx"
            or not candidate.is_file()
        ):
            raise HTTPException(status_code=404, detail="Report not found.")
        return FileResponse(
            candidate,
            filename=candidate.name,
            media_type=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
        )

    @app.get("/api/reports/catalog")
    async def report_catalog(request: Request):
        require_api_session(request)
        try:
            reports = await run_in_threadpool(
                request.app.state.report_workspace.catalog
            )
        except EPMError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "Report catalog is unavailable.",
                    "details": str(exc),
                },
            )
        return {
            "status": "success",
            "reports": [asdict(item) for item in reports],
        }

    @app.post("/api/reports/catalog")
    async def register_report(
        request: Request,
        payload: ReportRegistrationRequest,
    ):
        require_api_session(request)
        try:
            registered = await run_in_threadpool(
                request.app.state.report_workspace.register,
                payload.to_domain(),
            )
        except EPMError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "Report registration failed.",
                    "details": str(exc),
                },
            )
        return JSONResponse(
            status_code=201,
            content={
                "status": "success",
                "message": (
                    f"Report '{registered.name}' was registered."
                ),
                "report": asdict(registered),
            },
        )

    @app.post("/api/reports/preflight")
    async def report_preflight(
        request: Request,
        payload: ReportPreflightRequest,
    ):
        require_api_session(request)
        try:
            preflight = await run_in_threadpool(
                request.app.state.report_workspace.preflight,
                payload.form_name,
            )
        except EPMError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "Report preflight failed.",
                    "details": str(exc),
                },
            )
        return {
            "status": "success",
            "preflight": asdict(preflight),
        }

    @app.post("/api/operations/reports/runs")
    async def start_report_operation(
        request: Request,
        payload: ReportRunRequest,
    ):
        require_api_session(request)
        try:
            execution = request.app.state.operation_manager.submit(
                payload.to_domain(),
                actor=_request_actor(request),
            )
        except EPMError as exc:
            return _operation_start_error(exc)
        return _operation_accepted(execution.execution_id)

    @app.post("/api/processes/{process_code}/preflight")
    async def process_preflight(
        request: Request,
        process_code: str,
        payload: PlanningProcessRunRequest,
    ):
        require_api_session(request)
        try:
            result = await run_in_threadpool(
                request.app.state.process_service.preflight,
                payload.to_domain(process_code),
            )
        except EPMError as exc:
            LOGGER.warning(
                "Planning process preflight failed: process='%s', error=%s.",
                process_code,
                exc,
            )
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "Planning process preflight failed.",
                    "details": str(exc),
                },
            )
        return {
            "status": "success",
            "message": "Preflight completed successfully.",
            "preflight": asdict(result),
        }

    @app.post("/api/uploads")
    async def upload_process_file(
        request: Request,
        filename: str,
    ):
        owner = require_api_session(request)
        try:
            receipt = await request.app.state.upload_store.save(
                request,
                owner=owner,
                filename=filename,
            )
        except ConfigurationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "status": "success",
            "upload": asdict(receipt),
        }

    @app.post("/api/processes/{process_code}/runs")
    async def start_process_run(
        request: Request,
        process_code: str,
        payload: PlanningProcessRunRequest,
    ):
        owner = require_api_session(request)
        service = request.app.state.process_service
        upload_tokens = tuple(payload.pipeline_uploads.values())
        submitted = False
        try:
            preliminary = payload.to_domain(process_code)
            preflight = await run_in_threadpool(
                service.preflight,
                preliminary,
            )
            upload_paths = {
                key: request.app.state.upload_store.resolve(
                    token,
                    owner=owner,
                )
                for key, token in payload.pipeline_uploads.items()
            }
            _validate_process_files(
                preflight.file_requirements,
                upload_paths=upload_paths,
                inbox_files=payload.pipeline_inbox_files,
            )
            process_input = payload.to_domain(
                process_code,
                upload_paths=upload_paths,
            )
            execution = request.app.state.execution_manager.submit(
                process_input,
                cleanup=lambda: request.app.state.upload_store.delete_many(
                    upload_tokens
                ),
                actor=_request_actor(request),
            )
            submitted = True
        except EPMError as exc:
            status_code = (
                409 if "active execution" in str(exc) else 400
            )
            return JSONResponse(
                status_code=status_code,
                content={
                    "status": "error",
                    "message": "Planning process could not be started.",
                    "details": str(exc),
                },
            )
        finally:
            if not submitted:
                request.app.state.upload_store.delete_many(upload_tokens)
        return JSONResponse(
            status_code=202,
            content={
                "status": "accepted",
                "execution_id": execution.execution_id,
                "redirect": f"/app/runs/{execution.execution_id}",
            },
        )

    @app.get("/api/runs/{execution_id}")
    async def get_process_run(request: Request, execution_id: str):
        require_api_session(request)
        payload = _execution_payload(
            request.app.state.execution_manager,
            execution_id,
            resolved_settings,
        )
        if payload is None:
            raise HTTPException(status_code=404, detail="Execution not found.")
        return payload

    @app.get("/api/operations/catalog")
    async def operation_catalog(
        request: Request,
        include_live: bool = True,
    ):
        require_api_session(request)
        try:
            discovery = (
                request.app.state.operation_catalog.discover
                if include_live
                else request.app.state.operation_catalog.discover_registered
            )
            catalog = await run_in_threadpool(discovery)
        except EPMError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "Oracle operation catalog is unavailable.",
                    "details": str(exc),
                },
            )
        return {
            "status": "success",
            "catalog": asdict(catalog),
        }

    @app.get("/api/substitution-variables/catalog")
    async def substitution_variable_catalog(request: Request):
        require_api_session(request)
        try:
            catalog = await run_in_threadpool(
                request.app.state.substitution_variables.discover
            )
        except EPMError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": (
                        "Substitution-variable catalog is unavailable."
                    ),
                    "details": str(exc),
                },
            )
        return {
            "status": "success",
            "catalog": asdict(catalog),
        }

    @app.get("/api/user-variables/catalog")
    async def user_variable_catalog(request: Request, user_name: str | None = None):
        require_api_session(request)
        user = _current_user(request)
        assert user is not None
        target_user = str(user_name or user.username).strip()
        _require_user_variable_target(user, target_user)
        try:
            catalog = await run_in_threadpool(
                request.app.state.user_variables.discover,
                target_user,
            )
        except EPMError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "User-variable catalog is unavailable.",
                    "details": str(exc),
                },
            )
        return {"status": "success", "catalog": asdict(catalog)}

    @app.get("/api/operations/cube-refresh/catalog")
    async def cube_refresh_catalog(request: Request):
        require_api_session(request)
        try:
            jobs = await run_in_threadpool(
                request.app.state.operation_catalog.discover_job_names,
                job_type="CUBE_REFRESH",
            )
        except EPMError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "Cube Refresh catalog is unavailable.",
                    "details": str(exc),
                },
            )
        return {
            "status": "success",
            "jobs": jobs,
        }

    @app.get("/api/operations/data-maps/catalog")
    async def data_map_catalog(request: Request):
        require_api_session(request)
        try:
            jobs = await run_in_threadpool(
                request.app.state.operation_catalog.discover_job_names,
                job_type="PLAN_TYPE_MAP",
            )
        except EPMError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "Data Map catalog is unavailable.",
                    "details": str(exc),
                },
            )
        return {
            "status": "success",
            "jobs": jobs,
        }

    @app.get("/api/operations/business-rules/catalog")
    async def business_rule_catalog(request: Request):
        require_api_session(request)
        try:
            jobs = await run_in_threadpool(
                request.app.state.operation_catalog.discover_job_names,
                job_type="RULES",
            )
            registry_status = await run_in_threadpool(
                request.app.state.business_rule_rtp_registry.status,
                live_rule_names=jobs,
            )
        except EPMError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "Business Rule catalog is unavailable.",
                    "details": str(exc),
                },
            )
        return {
            "status": "success",
            "jobs": jobs,
            "rtp_registry": _business_rule_rtp_status_payload(
                registry_status
            ),
        }

    @app.get("/api/operations/business-rules/rtp-registry/status")
    async def business_rule_rtp_registry_status(request: Request):
        require_api_session(request)
        live_rules = None
        live_catalog_error = None
        try:
            live_rules = await run_in_threadpool(
                request.app.state.operation_catalog.discover_job_names,
                job_type="RULES",
            )
        except EPMError as exc:
            live_catalog_error = str(exc)
        registry_status = await run_in_threadpool(
            request.app.state.business_rule_rtp_registry.status,
            live_rule_names=live_rules,
        )
        return {
            "status": "success",
            "registry": _business_rule_rtp_status_payload(
                registry_status
            ),
            "live_catalog_error": live_catalog_error,
        }

    @app.get("/api/operations/business-rules/rtp-definition")
    async def business_rule_rtp_definition(
        request: Request,
        rule_name: str,
    ):
        require_api_session(request)
        try:
            definition = await run_in_threadpool(
                request.app.state.business_rule_rtp_registry.get_definition,
                rule_name,
            )
            latest_import = await run_in_threadpool(
                request.app.state.business_rule_rtp_registry.latest_import
            )
        except EPMError as exc:
            return _operation_start_error(exc)
        return {
            "status": "success",
            "definition": (
                _business_rule_rtp_payload(definition)
                if definition is not None
                else None
            ),
            "fallback": definition is None,
            "message": (
                "A synchronized Calc Manager RTP definition is available."
                if definition is not None
                else "No synchronized RTP definition exists for this rule; "
                "exact manual prompt names remain available."
            ),
            "latest_import": (
                _business_rule_rtp_import_payload(latest_import)
                if latest_import is not None
                else None
            ),
        }

    @app.post("/api/operations/business-rules/rtp-registry/import")
    async def import_business_rule_rtp_registry(
        request: Request,
        filename: str,
    ):
        require_api_session(request)
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > 50 * 1024 * 1024:
                    raise ValueError
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={
                        "status": "error",
                        "message": "The Calc Manager export exceeds the 50 MB limit.",
                    },
                )
        content = await request.body()
        try:
            result = await run_in_threadpool(
                request.app.state.business_rule_rtp_registry.import_package,
                filename,
                content,
            )
        except EPMError as exc:
            return _operation_start_error(exc)
        return {
            "status": "success",
            "message": (
                f"Imported {result.prompts_imported} runtime prompts for "
                f"{result.rules_imported} Business Rules: "
                f"{result.rules_added or 0} new, "
                f"{result.rules_changed or 0} changed, and "
                f"{result.rules_unchanged or 0} unchanged."
            ),
            "result": _business_rule_rtp_import_payload(result),
        }

    @app.get("/api/operations/data-integrations/catalog")
    async def data_integration_operation_catalog(request: Request):
        require_api_session(request)
        try:
            catalog, artifacts = await run_in_threadpool(
                _data_integration_catalog_payload,
                request.app.state.operation_catalog,
            )
        except EPMError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "Data Integration catalog is unavailable.",
                    "details": str(exc),
                },
            )
        return {
            "status": "success",
            "integrations": [
                asdict(item) for item in catalog.data_integrations
            ],
            "artifacts": [_oracle_artifact_payload(item) for item in artifacts],
        }

    @app.get("/api/operations/data-import/catalog")
    async def data_import_operation_catalog(request: Request):
        """Return live native Planning Import Data job names."""
        require_api_session(request)
        try:
            jobs = await run_in_threadpool(
                request.app.state.operation_catalog.discover_job_names,
                job_type="IMPORT_DATA",
            )
        except EPMError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "Planning Data Import catalog is unavailable.",
                    "details": str(exc),
                },
            )
        return {"status": "success", "jobs": jobs}

    @app.get("/api/operations/files/catalog")
    async def oracle_operation_file_catalog(
        request: Request,
        purpose: OracleFilePurpose,
    ):
        """Return live, operation-compatible Oracle Inbox files."""
        require_api_session(request)
        try:
            catalog = await run_in_threadpool(
                request.app.state.oracle_file_catalog.discover,
                purpose,
            )
        except EPMError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "Oracle Inbox files are unavailable.",
                    "details": str(exc),
                },
            )
        return {
            "status": "success",
            "purpose": catalog.purpose.value,
            "files": [asdict(item) for item in catalog.files],
        }

    @app.get("/api/operations/metadata-import/catalog")
    async def metadata_import_operation_catalog(request: Request):
        """Return live Metadata Import and optional refresh job names."""
        require_api_session(request)
        try:
            jobs_by_type = await run_in_threadpool(
                request.app.state.operation_catalog.discover_job_names_for_types,
                ("IMPORT_METADATA", "CUBE_REFRESH"),
            )
        except EPMError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "Metadata Import catalog is unavailable.",
                    "details": str(exc),
                },
            )
        return {
            "status": "success",
            "jobs": jobs_by_type["IMPORT_METADATA"],
            "refresh_jobs": jobs_by_type["CUBE_REFRESH"],
        }

    @app.get("/api/operations/pipelines/catalog")
    async def pipeline_operation_catalog(request: Request):
        """Return registered Pipelines without contacting Oracle."""
        require_api_session(request)
        try:
            catalog, artifacts = await run_in_threadpool(
                _pipeline_catalog_payload,
                request.app.state.operation_catalog,
            )
        except EPMError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "Pipeline catalog is unavailable.",
                    "details": str(exc),
                },
            )
        return {
            "status": "success",
            "pipelines": [asdict(item) for item in catalog.pipelines],
            "artifacts": [_oracle_artifact_payload(item) for item in artifacts],
        }

    @app.post("/api/operations/oracle-catalog/sync")
    async def synchronize_oracle_artifact_catalog(request: Request):
        """Synchronize all safely discoverable Oracle artifacts."""
        require_api_session(request)
        result = await run_in_threadpool(
            request.app.state.operation_catalog.synchronize_artifacts
        )
        artifacts = request.app.state.operation_catalog.synchronized_catalog()
        return {
            "status": "success",
            "sync": asdict(result),
            "artifacts": [
                _oracle_artifact_payload(item) for item in artifacts
            ],
        }

    @app.get("/api/operations/oracle-catalog")
    async def oracle_artifact_catalog(request: Request):
        """Return the last synchronized environment catalog without Oracle I/O."""
        require_api_session(request)
        artifacts = await run_in_threadpool(
            request.app.state.operation_catalog.synchronized_catalog
        )
        verified = sum(item.is_verified for item in artifacts)
        attention = sum(
            item.status
            in {
                OracleArtifactStatus.MISSING,
                OracleArtifactStatus.UNAVAILABLE,
                OracleArtifactStatus.INACTIVE,
            }
            for item in artifacts
        )
        last_synchronized_at = max(
            (
                item.last_verified_at
                for item in artifacts
                if item.last_verified_at is not None
            ),
            default=None,
        )
        return {
            "status": "success",
            "environment": {
                "application_name": request.app.state.settings.application_name,
                "deployment_mode": (
                    request.app.state.settings.resolved_deployment_mode
                ),
            },
            "summary": {
                "verified": verified,
                "attention": attention,
                "last_synchronized_at": (
                    last_synchronized_at.isoformat()
                    if last_synchronized_at is not None
                    else None
                ),
            },
            "artifacts": [
                _oracle_artifact_payload(item) for item in artifacts
            ],
        }

    @app.post("/api/operations/business-rules/runs")
    async def start_business_rule(
        request: Request,
        payload: BusinessRuleRunRequest,
    ):
        require_api_session(request)
        try:
            task_link = _planning_task_link_callback(
                request,
                payload.planning_task_id,
                action_type="RUN_BUSINESS_RULE",
            )
            available_rules = await run_in_threadpool(
                request.app.state.operation_catalog.discover_job_names,
                job_type="RULES",
            )
            _require_live_artifact(
                payload.rule_name,
                available_rules,
                label="Business Rule",
            )
            normalized_prompts = await run_in_threadpool(
                request.app.state.business_rule_rtp_registry.normalize_for_execution,
                payload.rule_name,
                payload.runtime_prompts,
            )
            execution = request.app.state.operation_manager.submit(
                BusinessRuleOperationInput(
                    rule_name=payload.rule_name,
                    runtime_prompts=normalized_prompts,
                ),
                actor=_request_actor(request),
                on_queued=task_link,
            )
        except EPMError as exc:
            return _operation_start_error(exc)
        return _operation_accepted(
            execution.execution_id,
            planning_task_id=payload.planning_task_id,
        )

    @app.post("/api/operations/data-maps/runs")
    async def start_data_map(
        request: Request,
        payload: DataMapRunRequest,
    ):
        require_api_session(request)
        try:
            task_link = _planning_task_link_callback(
                request,
                payload.planning_task_id,
                action_type="RUN_DATA_MAP",
            )
            available_maps = await run_in_threadpool(
                request.app.state.operation_catalog.discover_job_names,
                job_type="PLAN_TYPE_MAP",
            )
            _require_live_artifact(
                payload.data_map_name,
                available_maps,
                label="Data Map",
            )
            execution = request.app.state.operation_manager.submit(
                payload.to_domain(),
                actor=_request_actor(request),
                on_queued=task_link,
            )
        except EPMError as exc:
            return _operation_start_error(exc)
        return _operation_accepted(
            execution.execution_id,
            planning_task_id=payload.planning_task_id,
        )

    @app.get("/api/operations/pipelines/{pipeline_code}/preflight")
    async def pipeline_operation_preflight(
        request: Request,
        pipeline_code: str,
    ):
        require_api_session(request)
        try:
            preview = await run_in_threadpool(
                request.app.state.operation_catalog.preflight_pipeline,
                pipeline_code,
            )
        except EPMError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "Pipeline preflight failed.",
                    "details": str(exc),
                },
            )
        return {
            "status": "success",
            "preview": asdict(preview),
        }

    @app.get("/api/v1/excel/pipelines/{pipeline_code}/preflight")
    async def excel_pipeline_preflight(
        request: Request,
        pipeline_code: str,
    ):
        """Inspect one registered Pipeline for a trusted Excel client."""
        authenticated = require_bearer_token(
            request,
            scope=ApiTokenScope.PIPELINE_READ,
            permission=Permission.OPERATION_EXECUTE,
        )
        try:
            preview = await run_in_threadpool(
                request.app.state.operation_catalog.preflight_pipeline,
                pipeline_code,
            )
        except EPMError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "Excel Pipeline preflight failed.",
                    "details": str(exc),
                },
            )
        return {
            "status": "success",
            "requested_by": authenticated.user.display_name,
            "pipeline": asdict(preview),
        }

    @app.post(
        "/api/v1/excel/pipelines/{pipeline_code}/runs",
        status_code=202,
    )
    async def excel_pipeline_run(
        request: Request,
        pipeline_code: str,
        payload: ExcelPipelineRunRequest,
    ):
        """Validate and queue an Inbox-based Pipeline from Excel."""
        authenticated = require_bearer_token(
            request,
            scope=ApiTokenScope.PIPELINE_RUN,
            permission=Permission.OPERATION_EXECUTE,
        )
        try:
            preview = await run_in_threadpool(
                request.app.state.operation_catalog.preflight_pipeline,
                pipeline_code,
            )
            _validate_operation_pipeline_files(
                preview.file_requirements,
                upload_paths={},
                inbox_files=payload.inbox_files,
            )
            _validate_excel_pipeline_variables(
                preview,
                variables=payload.variables,
                inbox_files=payload.inbox_files,
            )
            execution = request.app.state.operation_manager.submit(
                payload.to_domain(preview.code),
                actor=ExecutionActor(
                    username=authenticated.user.username,
                    display_name=authenticated.user.display_name,
                    trigger_source=TriggerSource.EXCEL,
                ),
            )
        except EPMError as exc:
            return _operation_start_error(exc)
        return {
            "status": "accepted",
            "execution_id": execution.execution_id,
            "pipeline_code": preview.code,
            "status_url": (
                f"/api/v1/excel/executions/{execution.execution_id}"
            ),
        }

    @app.get("/api/v1/excel/executions/{execution_id}")
    async def excel_pipeline_execution(
        request: Request,
        execution_id: str,
    ):
        """Return concise status for an Excel-submitted execution."""
        require_bearer_token(
            request,
            scope=ApiTokenScope.EXECUTION_READ,
            permission=Permission.HISTORY_VIEW,
        )
        payload = _operation_execution_payload(
            request.app.state.operation_manager,
            execution_id,
            request.app.state.settings,
        )
        if payload is None:
            raise HTTPException(status_code=404, detail="Execution not found.")
        return payload

    @app.post("/api/operations/pipelines/register")
    async def register_pipeline(
        request: Request,
        payload: PipelineRegistrationRequest,
    ):
        require_api_session(request)
        try:
            preview = await run_in_threadpool(
                request.app.state.operation_catalog.register_pipeline,
                payload.pipeline_code,
            )
        except EPMError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "Pipeline could not be registered.",
                    "details": str(exc),
                },
            )
        return {
            "status": "success",
            "message": (
                f"{preview.display_name} ({preview.code}) was verified "
                "and registered."
            ),
            "pipeline": {
                "code": preview.code,
                "name": preview.display_name,
                "description": "Verified from Oracle EPM",
            },
            "preview": asdict(preview),
        }

    @app.post("/api/operations/data-integrations/register")
    async def register_data_integration(
        request: Request,
        payload: DataIntegrationRegistrationRequest,
    ):
        require_api_session(request)
        try:
            artifact = await run_in_threadpool(
                request.app.state.operation_catalog.register_data_integration,
                payload.integration_name,
                description=payload.description,
            )
        except EPMError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "Data Integration could not be registered.",
                    "details": str(exc),
                },
            )
        return {
            "status": "success",
            "message": (
                f"{artifact.oracle_identifier} is registered. Because Oracle "
                "does not expose a read-only Integration lookup, its first "
                "governed run will complete verification."
            ),
            "integration": _oracle_artifact_payload(artifact),
        }

    @app.get("/api/process-designer/definitions")
    async def process_designer_definitions(request: Request):
        require_api_session(request)
        return {
            "status": "success",
            "definitions": [
                asdict(item)
                for item in request.app.state.process_designer.list_processes()
            ],
        }

    @app.get(
        "/api/process-designer/{process_code}/architecture-review"
    )
    async def process_architecture_review(
        request: Request,
        process_code: str,
    ):
        """Return an offline, read-only Process ownership assessment."""
        require_api_session(request)
        try:
            review = request.app.state.process_designer.architecture_review(
                process_code
            )
        except EPMError as exc:
            return JSONResponse(
                status_code=404,
                content={
                    "status": "error",
                    "message": "Process architecture could not be reviewed.",
                    "details": str(exc),
                },
            )
        payload = asdict(review)
        payload["aligned"] = review.aligned
        payload["requires_review"] = review.requires_review
        payload["pipeline_accepts_year"] = review.pipeline_accepts_year
        payload["can_prepare_migration"] = review.can_prepare_migration
        return {"status": "success", "review": payload}

    @app.post(
        "/api/process-designer/{process_code}/migration-draft"
    )
    async def prepare_process_migration_draft(
        request: Request,
        process_code: str,
    ):
        """Create or reuse an offline thin draft for later activation."""
        require_api_session(request)
        try:
            version = await run_in_threadpool(
                request.app.state.process_designer.prepare_migration_draft,
                process_code,
            )
        except EPMError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "A simplified draft could not be prepared.",
                    "details": str(exc),
                },
            )
        return {
            "status": "success",
            "message": (
                f"Simplified draft v{version.version} is ready. No Oracle "
                "configuration was changed."
            ),
            "definition": asdict(version),
        }

    @app.post("/api/process-designer/drafts")
    async def save_process_designer_draft(
        request: Request,
        payload: PipelineProcessDraftRequest,
    ):
        require_api_session(request)
        try:
            version = await run_in_threadpool(
                request.app.state.process_designer.save_draft,
                payload.to_domain(),
            )
        except EPMError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "Process draft could not be saved.",
                    "details": str(exc),
                },
            )
        return {
            "status": "success",
            "message": (
                f"Draft {version.process.code} version "
                f"{version.version} was saved."
            ),
            "definition": asdict(version),
        }

    @app.post(
        "/api/process-designer/{process_code}/versions/{version}/activate"
    )
    async def activate_process_designer_version(
        request: Request,
        process_code: str,
        version: int,
    ):
        require_api_session(request)
        try:
            activated = await run_in_threadpool(
                request.app.state.process_designer.activate,
                process_code,
                version,
            )
        except EPMError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "Process version could not be activated.",
                    "details": str(exc),
                },
            )
        return {
            "status": "success",
            "message": (
                f"{activated.process.display_name} version "
                f"{activated.version} is active."
            ),
            "definition": asdict(activated),
        }

    @app.put("/api/process-designer/{process_code}")
    async def revise_pipeline_process(
        request: Request,
        process_code: str,
        payload: PipelineProcessDraftRequest,
    ):
        require_api_session(request)
        try:
            revision = await run_in_threadpool(
                request.app.state.process_designer.save_revision,
                process_code,
                payload.to_domain(),
            )
        except EPMError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "Process changes could not be saved.",
                    "details": str(exc),
                },
            )
        return {
            "status": "success",
            "message": (
                f"Draft v{revision.version} was saved. Activate it when "
                "the change is ready."
            ),
            "definition": asdict(revision),
        }

    @app.delete("/api/process-designer/{process_code}")
    async def deactivate_pipeline_process(
        request: Request,
        process_code: str,
    ):
        require_api_session(request)
        try:
            deactivated = await run_in_threadpool(
                request.app.state.process_designer.deactivate,
                process_code,
            )
        except EPMError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "Process could not be deactivated.",
                    "details": str(exc),
                },
            )
        return {
            "status": "success",
            "message": (
                f"The published version of "
                f"{deactivated.process.display_name} was deactivated. "
                "Its definitions, run presets, and history were retained."
            ),
        }

    @app.post("/api/process-designer/{process_code}/presets")
    @app.post("/api/process-designer/{process_code}/profiles")
    async def save_pipeline_run_profile(
        request: Request,
        process_code: str,
        payload: PipelineRunProfileRequest,
    ):
        require_api_session(request)
        try:
            profile = await run_in_threadpool(
                request.app.state.process_designer.save_profile,
                process_code,
                payload.to_domain(),
            )
        except EPMError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "Run preset could not be saved.",
                    "details": str(exc),
                },
            )
        profile_payload = asdict(profile)
        profile_payload["one_click_ready"] = profile.one_click_ready
        return {
            "status": "success",
            "message": f"Run preset '{profile.name}' was saved.",
            "profile": profile_payload,
        }

    @app.post(
        "/api/process-designer/{process_code}/presets/"
        "{profile_id}/runs"
    )
    @app.post(
        "/api/process-designer/{process_code}/profiles/"
        "{profile_id}/runs"
    )
    async def run_pipeline_profile(
        request: Request,
        process_code: str,
        profile_id: int,
        payload: PipelineRunProfileExecutionRequest,
    ):
        owner = require_api_session(request)
        designer = request.app.state.process_designer
        upload_tokens = tuple(payload.uploads.values())
        submitted = False
        try:
            profile = designer.get_profile(process_code, profile_id)
            await run_in_threadpool(
                designer.validate_profile,
                process_code,
                profile_id,
            )
            supplied_keys = {key.casefold() for key in payload.uploads}
            expected_keys = {
                key.casefold() for key in profile.required_upload_keys
            }
            if supplied_keys != expected_keys:
                missing = expected_keys - supplied_keys
                unexpected = supplied_keys - expected_keys
                details = []
                if missing:
                    details.append(
                        "missing " + ", ".join(sorted(missing))
                    )
                if unexpected:
                    details.append(
                        "unexpected " + ", ".join(sorted(unexpected))
                    )
                raise ConfigurationError(
                    "Runtime preset uploads do not match the saved "
                    "strategy: " + "; ".join(details)
                )
            upload_paths = {
                key: request.app.state.upload_store.resolve(
                    token,
                    owner=owner,
                )
                for key, token in payload.uploads.items()
            }
            process_input = designer.profile_process_input(
                process_code,
                profile_id,
                upload_paths=upload_paths,
            )
            preflight = await run_in_threadpool(
                request.app.state.process_service.preflight,
                process_input,
            )
            _validate_process_files(
                preflight.file_requirements,
                upload_paths=upload_paths,
                inbox_files=process_input.pipeline_inbox_files,
            )
            execution = request.app.state.execution_manager.submit(
                process_input,
                cleanup=lambda: request.app.state.upload_store.delete_many(
                    upload_tokens
                ),
                actor=_request_actor(request),
            )
            submitted = True
        except EPMError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "message": "Run preset could not be started.",
                    "details": str(exc),
                },
            )
        finally:
            if not submitted:
                request.app.state.upload_store.delete_many(upload_tokens)
        return JSONResponse(
            status_code=202,
            content={
                "status": "accepted",
                "execution_id": execution.execution_id,
                "redirect": f"/app/runs/{execution.execution_id}",
            },
        )

    @app.delete(
        "/api/process-designer/{process_code}/presets/{profile_id}"
    )
    @app.delete(
        "/api/process-designer/{process_code}/profiles/{profile_id}"
    )
    async def archive_pipeline_run_profile(
        request: Request,
        process_code: str,
        profile_id: int,
    ):
        require_api_session(request)
        try:
            profile = await run_in_threadpool(
                request.app.state.process_designer.archive_profile,
                process_code,
                profile_id,
            )
        except EPMError as exc:
            return JSONResponse(
                status_code=404,
                content={
                    "status": "error",
                    "message": "Run preset could not be removed.",
                    "details": str(exc),
                },
            )
        return {
            "status": "success",
            "message": f"Run preset '{profile.name}' was removed.",
        }

    @app.post("/api/operations/pipelines/runs")
    async def start_pipeline_operation(
        request: Request,
        payload: PipelineRunRequest,
    ):
        owner = require_api_session(request)
        upload_tokens = tuple(payload.uploads.values())
        submitted = False
        try:
            task_link = _planning_task_link_callback(
                request,
                payload.planning_task_id,
                action_type="RUN_PIPELINE",
            )
            preview = await run_in_threadpool(
                request.app.state.operation_catalog.preflight_pipeline,
                payload.pipeline_code,
            )
            upload_paths = {
                key: request.app.state.upload_store.resolve(
                    token,
                    owner=owner,
                )
                for key, token in payload.uploads.items()
            }
            _validate_operation_pipeline_files(
                preview.file_requirements,
                upload_paths=upload_paths,
                inbox_files=payload.inbox_files,
            )
            execution = request.app.state.operation_manager.submit(
                payload.to_domain(upload_paths=upload_paths),
                cleanup=lambda: request.app.state.upload_store.delete_many(
                    upload_tokens
                ),
                actor=_request_actor(request),
                on_queued=task_link,
            )
            submitted = True
        except EPMError as exc:
            return _operation_start_error(exc)
        finally:
            if not submitted:
                request.app.state.upload_store.delete_many(upload_tokens)
        return _operation_accepted(
            execution.execution_id,
            planning_task_id=payload.planning_task_id,
        )

    @app.post("/api/operations/data-integrations/runs")
    async def start_data_integration_operation(
        request: Request,
        payload: DataIntegrationRunRequest,
    ):
        owner = require_api_session(request)
        upload_tokens = (
            (payload.upload_token,) if payload.upload_token else ()
        )
        submitted = False
        try:
            task_link = _planning_task_link_callback(
                request,
                payload.planning_task_id,
                action_type="RUN_DATA_INTEGRATION",
            )
            await run_in_threadpool(
                request.app.state.operation_catalog.require_data_integration,
                payload.integration_name,
            )
            upload_path = (
                request.app.state.upload_store.resolve(
                    payload.upload_token,
                    owner=owner,
                )
                if payload.upload_token
                else None
            )
            if (
                upload_path is not None
                and upload_path.suffix.casefold()
                not in {".csv", ".txt", ".zip", ".dat"}
            ):
                raise ConfigurationError(
                    "Data Integration uploads support .csv, .txt, .zip, "
                    "or .dat."
                )
            execution = request.app.state.operation_manager.submit(
                payload.to_domain(upload_path=upload_path),
                cleanup=lambda: request.app.state.upload_store.delete_many(
                    upload_tokens
                ),
                actor=_request_actor(request),
                on_queued=task_link,
            )
            submitted = True
        except EPMError as exc:
            return _operation_start_error(exc)
        finally:
            if not submitted:
                request.app.state.upload_store.delete_many(upload_tokens)
        return _operation_accepted(
            execution.execution_id,
            planning_task_id=payload.planning_task_id,
        )

    @app.post("/api/operations/metadata-import/runs")
    async def start_metadata_import_operation(
        request: Request,
        payload: MetadataImportRunRequest,
    ):
        owner = require_api_session(request)
        upload_tokens = (
            (payload.upload_token,) if payload.upload_token else ()
        )
        submitted = False
        try:
            task_link = _planning_task_link_callback(
                request,
                payload.planning_task_id,
                action_type="RUN_METADATA_IMPORT",
            )
            available_jobs = await run_in_threadpool(
                request.app.state.operation_catalog.discover_job_names,
                job_type="IMPORT_METADATA",
            )
            _require_live_artifact(
                payload.job_name,
                available_jobs,
                label="Metadata Import job",
            )
            if payload.refresh_job_name:
                available_refresh_jobs = await run_in_threadpool(
                    request.app.state.operation_catalog.discover_job_names,
                    job_type="CUBE_REFRESH",
                )
                _require_live_artifact(
                    payload.refresh_job_name,
                    available_refresh_jobs,
                    label="Cube Refresh job",
                )
            upload_path = (
                request.app.state.upload_store.resolve(
                    payload.upload_token,
                    owner=owner,
                )
                if payload.upload_token
                else None
            )
            if (
                upload_path is not None
                and upload_path.suffix.casefold() not in {".csv", ".zip"}
            ):
                raise ConfigurationError(
                    "Metadata Import uploads support .csv or .zip."
                )
            execution = request.app.state.operation_manager.submit(
                payload.to_domain(upload_path=upload_path),
                cleanup=lambda: request.app.state.upload_store.delete_many(
                    upload_tokens
                ),
                actor=_request_actor(request),
                on_queued=task_link,
            )
            submitted = True
        except EPMError as exc:
            return _operation_start_error(exc)
        finally:
            if not submitted:
                request.app.state.upload_store.delete_many(upload_tokens)
        return _operation_accepted(
            execution.execution_id,
            planning_task_id=payload.planning_task_id,
        )

    @app.post("/api/operations/data-import/runs")
    async def start_data_import_operation(
        request: Request,
        payload: DataImportRunRequest,
    ):
        owner = require_api_session(request)
        upload_tokens = (
            (payload.upload_token,) if payload.upload_token else ()
        )
        submitted = False
        try:
            task_link = _planning_task_link_callback(
                request,
                payload.planning_task_id,
                action_type="RUN_DATA_IMPORT",
            )
            available_jobs = await run_in_threadpool(
                request.app.state.operation_catalog.discover_job_names,
                job_type="IMPORT_DATA",
            )
            _require_live_artifact(
                payload.job_name,
                available_jobs,
                label="Planning Data Import job",
            )
            upload_path = (
                request.app.state.upload_store.resolve(
                    payload.upload_token,
                    owner=owner,
                )
                if payload.upload_token
                else None
            )
            if (
                upload_path is not None
                and upload_path.suffix.casefold()
                not in {".csv", ".txt", ".zip"}
            ):
                raise ConfigurationError(
                    "Planning Data Import uploads support .csv, .txt, "
                    "or .zip."
                )
            execution = request.app.state.operation_manager.submit(
                payload.to_domain(upload_path=upload_path),
                cleanup=lambda: request.app.state.upload_store.delete_many(
                    upload_tokens
                ),
                actor=_request_actor(request),
                on_queued=task_link,
            )
            submitted = True
        except EPMError as exc:
            return _operation_start_error(exc)
        finally:
            if not submitted:
                request.app.state.upload_store.delete_many(upload_tokens)
        return _operation_accepted(
            execution.execution_id,
            planning_task_id=payload.planning_task_id,
        )

    @app.post("/api/operations/substitution-variables/runs")
    async def start_substitution_variable_operation(
        request: Request,
        payload: SubstitutionVariableRunRequest,
    ):
        require_api_session(request)
        try:
            task_link = _planning_task_link_callback(
                request,
                payload.planning_task_id,
                action_type="UPDATE_SUBSTITUTION_VARIABLE",
            )
            execution = request.app.state.operation_manager.submit(
                payload.to_domain(),
                actor=_request_actor(request),
                on_queued=task_link,
            )
        except EPMError as exc:
            return _operation_start_error(exc)
        return _operation_accepted(
            execution.execution_id,
            planning_task_id=payload.planning_task_id,
        )

    @app.post("/api/operations/user-variables/runs")
    async def start_user_variable_operation(
        request: Request,
        payload: UserVariableRunRequest,
    ):
        require_api_session(request)
        user = _current_user(request)
        assert user is not None
        _require_user_variable_target(user, payload.user_name)
        try:
            task_link = (
                _planning_task_link_callback(
                    request,
                    payload.planning_task_id,
                    action_type="UPDATE_USER_VARIABLE",
                )
                if payload.planning_task_id is not None
                else None
            )
            execution = request.app.state.operation_manager.submit(
                payload.to_domain(),
                actor=_request_actor(request),
                on_queued=task_link,
            )
        except EPMError as exc:
            return _operation_start_error(exc)
        return _operation_accepted(
            execution.execution_id,
            planning_task_id=payload.planning_task_id,
        )

    @app.post("/api/operations/cube-refresh/runs")
    async def start_cube_refresh_operation(
        request: Request,
        payload: CubeRefreshRunRequest,
    ):
        require_api_session(request)
        try:
            task_link = _planning_task_link_callback(
                request,
                payload.planning_task_id,
                action_type="RUN_CUBE_REFRESH",
            )
            execution = request.app.state.operation_manager.submit(
                payload.to_domain(),
                actor=_request_actor(request),
                on_queued=task_link,
            )
        except EPMError as exc:
            return _operation_start_error(exc)
        return _operation_accepted(
            execution.execution_id,
            planning_task_id=payload.planning_task_id,
        )

    @app.get(
        "/app/operations/runs/{execution_id}",
        include_in_schema=False,
    )
    async def operation_run_page(
        request: Request,
        execution_id: str,
    ):
        return modern_workspace_redirect(
            request, "jobs", execution_id=execution_id
        )

    @app.get("/api/operations/runs/{execution_id}")
    async def get_operation_run(request: Request, execution_id: str):
        require_api_session(request)
        _require_operation_execution_access(request, execution_id)
        payload = _operation_execution_payload(
            request.app.state.operation_manager,
            execution_id,
            request.app.state.settings,
        )
        if payload is None:
            raise HTTPException(status_code=404, detail="Execution not found.")
        return payload

    @app.get("/api/operations/runs/{execution_id}/recovery")
    async def get_standalone_flow_recovery(
        request: Request,
        execution_id: str,
    ):
        require_api_session(request)
        _require_operation_execution_access(request, execution_id)
        user = _current_user(request)
        if user is None or not user.has_permission(
            Permission.OPERATION_EXECUTE
        ):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Only an authorized operation executor can recover a "
                    "failed standalone flow."
                ),
            )
        try:
            plan = await run_in_threadpool(
                request.app.state.flow_recovery.plan,
                execution_id,
            )
        except EPMError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"status": "success", "recovery": asdict(plan)}

    @app.post("/api/operations/runs/{execution_id}/recovery")
    async def retry_standalone_flow(
        request: Request,
        execution_id: str,
        payload: StandaloneFlowRecoveryRunRequest,
    ):
        owner = require_api_session(request)
        _require_operation_execution_access(request, execution_id)
        user = _current_user(request)
        if user is None or not user.has_permission(
            Permission.OPERATION_EXECUTE
        ):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Only an authorized operation executor can recover a "
                    "failed standalone flow."
                ),
            )
        upload_tokens = tuple(payload.replacement_uploads.values())
        submitted = False
        try:
            replacement_paths = {
                key: request.app.state.upload_store.resolve(
                    token,
                    owner=owner,
                )
                for key, token in payload.replacement_uploads.items()
            }
            recovered = await run_in_threadpool(
                request.app.state.flow_recovery.prepare_retry,
                execution_id,
                expected_failed_step=payload.failed_step_sequence,
                replacement_uploads=replacement_paths,
            )
            execution = request.app.state.operation_manager.submit_flow(
                recovered,
                cleanup=lambda: request.app.state.upload_store.delete_many(
                    upload_tokens
                ),
                actor=_request_actor(request),
            )
            submitted = True
        except EPMError as exc:
            return _operation_start_error(exc)
        finally:
            if upload_tokens and not submitted:
                request.app.state.upload_store.delete_many(upload_tokens)
        return JSONResponse(
            status_code=202,
            content={
                "status": "accepted",
                "execution_id": execution.execution_id,
                "source_execution_id": execution_id,
                "redirect": (
                    f"/app/operations/runs/{execution.execution_id}"
                ),
            },
        )

    @app.get(
        "/app/operations/runs/{execution_id}/log",
        include_in_schema=False,
        response_class=FileResponse,
    )
    async def download_operation_log(
        request: Request,
        execution_id: str,
    ):
        redirect = require_session(request)
        if redirect is not None:
            return redirect
        _require_operation_execution_access(request, execution_id)
        execution = request.app.state.operation_manager.get(execution_id)
        if execution is None or not execution.log_file.is_file():
            raise HTTPException(
                status_code=404,
                detail="Execution log is not available.",
            )
        return FileResponse(
            execution.log_file,
            filename=f"{execution_id}.log",
            media_type="text/plain",
        )

    @app.get(
        "/app/runs/{execution_id}/log",
        include_in_schema=False,
        response_class=FileResponse,
    )
    async def download_process_log(request: Request, execution_id: str):
        redirect = require_session(request)
        if redirect is not None:
            return redirect
        execution = request.app.state.execution_manager.get(execution_id)
        if execution is None or not execution.log_file.is_file():
            raise HTTPException(
                status_code=404,
                detail="Execution log is not available.",
            )
        return FileResponse(
            execution.log_file,
            filename=f"{execution_id}.log",
            media_type="text/plain",
        )

    return app


def _secure_cookie_enabled() -> bool:
    """Return whether the web session cookie requires HTTPS."""
    value = os.getenv("WEB_SECURE_COOKIES", "false").strip().casefold()
    if value in {"true", "1", "yes", "on"}:
        return True
    if value in {"false", "0", "no", "off"}:
        return False
    raise ValueError("WEB_SECURE_COOKIES must be true or false.")


def _validate_process_files(
    requirements,
    *,
    upload_paths: dict[str, Path],
    inbox_files: dict[str, str],
) -> None:
    """Validate uploaded and Inbox file choices against live requirements."""
    known = {
        requirement.key.casefold(): requirement
        for requirement in requirements
    }
    supplied_uploads = {
        key.casefold(): (key, path)
        for key, path in upload_paths.items()
    }
    supplied_inbox = {
        key.casefold(): (key, value)
        for key, value in inbox_files.items()
    }
    unknown = sorted(
        (
            set(supplied_uploads) | set(supplied_inbox)
        ) - set(known)
    )
    if unknown:
        raise ConfigurationError(
            "File input(s) do not match the selected Pipeline: "
            + ", ".join(unknown)
        )
    duplicate = set(supplied_uploads) & set(supplied_inbox)
    if duplicate:
        raise ConfigurationError(
            "A Pipeline file input cannot use both an upload and an Inbox "
            "file: " + ", ".join(sorted(duplicate))
        )
    for key, requirement in known.items():
        upload = supplied_uploads.get(key)
        inbox = supplied_inbox.get(key)
        if (
            requirement.required
            and upload is None
            and inbox is None
            and not requirement.configured_reference
        ):
            raise ConfigurationError(
                f"Pipeline file input '{requirement.display_name}' is "
                "required."
            )
        if upload is not None and requirement.allowed_extensions:
            extension = upload[1].suffix.casefold()
            if extension not in requirement.allowed_extensions:
                raise ConfigurationError(
                    f"Pipeline file input '{requirement.display_name}' "
                    "supports: "
                    + ", ".join(sorted(requirement.allowed_extensions))
                )


def _execution_payload(manager, execution_id: str, settings: Settings):
    """Combine temporary background state with durable workflow history."""
    managed = manager.get(execution_id)
    workflow = manager.get_workflow(execution_id)
    if managed is None and workflow is None:
        return None
    if workflow is not None:
        steps = [
            {
                "name": step.name,
                "sequence": step.sequence,
                "status": step.status.value,
                "started_at": (
                    step.started_at.isoformat() if step.started_at else None
                ),
                "completed_at": (
                    step.completed_at.isoformat()
                    if step.completed_at
                    else None
                ),
                "details": step.details,
                "error_message": step.error_message,
            }
            for step in workflow.steps
        ]
        status = workflow.status.value
        started_at = workflow.started_at.isoformat()
        completed_at = (
            workflow.completed_at.isoformat()
            if workflow.completed_at
            else None
        )
        error_message = workflow.error_message
        initiated_by = workflow.initiated_by_display or workflow.initiated_by
        trigger_source = workflow.trigger_source.value
        executed_by = workflow.oracle_execution_username
    else:
        steps = []
        status = managed.status.value
        started_at = managed.submitted_at.isoformat()
        completed_at = None
        error_message = managed.error_message
        initiated_by = None
        trigger_source = None
        executed_by = settings.oracle_execution_username

    terminal_steps = sum(
        item["status"]
        in {
            WorkflowStepStatus.SUCCESS.value,
            WorkflowStepStatus.FAILED.value,
            WorkflowStepStatus.SKIPPED.value,
        }
        for item in steps
    )
    artifacts: list[dict[str, str]] = []
    report_root = settings.report_output_dir.resolve()
    for step in steps:
        raw_output = step["details"].get("output_path")
        if not raw_output:
            continue
        path = Path(str(raw_output)).resolve()
        if (
            path.parent == report_root
            and path.suffix.casefold() == ".xlsx"
            and path.is_file()
        ):
            artifacts.append(
                {
                    "name": path.name,
                    "url": f"/app/reports/{path.name}",
                }
            )
    return {
        "execution_id": execution_id,
        "process_code": (
            workflow.workflow_name
            if workflow is not None
            else managed.process_code
        ),
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "error_message": error_message,
        "initiated_by": initiated_by,
        "trigger_source": trigger_source,
        "executed_by": executed_by,
        "steps": steps,
        "completed_steps": terminal_steps,
        "total_steps": len(steps),
        "artifacts": artifacts,
        "log_url": (
            f"/app/runs/{execution_id}/log"
            if managed is not None and managed.log_file.is_file()
            else None
        ),
        "terminal": status in {"SUCCESS", "FAILED", "RECOVERY_REQUIRED"},
    }


def _pipeline_catalog_payload(service: OperationCatalogService):
    artifacts = service.registered_artifacts(
        OracleArtifactType.PIPELINE,
        include_inactive=True,
    )
    return (
        service.discover_registered(),
        artifacts if isinstance(artifacts, tuple) else (),
    )


def _data_integration_catalog_payload(service: OperationCatalogService):
    artifacts = service.registered_artifacts(
        OracleArtifactType.DATA_INTEGRATION,
        include_inactive=True,
    )
    return (
        service.discover_registered(),
        artifacts if isinstance(artifacts, tuple) else (),
    )


def _oracle_artifact_payload(artifact) -> dict:
    return {
        "artifact_id": artifact.artifact_id,
        "artifact_type": artifact.artifact_type.value,
        "oracle_identifier": artifact.oracle_identifier,
        "display_name": artifact.display_name,
        "description": artifact.description,
        "source": artifact.source.value,
        "status": artifact.status.value,
        "is_active": artifact.is_active,
        "is_runnable": artifact.is_runnable,
        "is_verified": artifact.is_verified,
        "consecutive_missing_count": artifact.consecutive_missing_count,
        "last_verified_at": (
            artifact.last_verified_at.isoformat()
            if artifact.last_verified_at is not None
            else None
        ),
        "last_error": artifact.last_error,
    }


def _business_rule_rtp_payload(definition) -> dict[str, object]:
    """Serialize a current RTP contract without exposing raw source XML."""
    return {
        "rule_name": definition.rule_name,
        "cube_name": definition.cube_name,
        "source_name": definition.source_name,
        "source_checksum": definition.source_checksum,
        "parser_version": definition.parser_version,
        "definition_checksum": definition.definition_checksum,
        "synchronized_at": definition.synchronized_at.isoformat(),
        "prompts": [
            {
                "name": prompt.name,
                "label": prompt.label,
                "order": prompt.prompt_order,
                "value_type": prompt.value_type,
                "dimension": prompt.dimension,
                "default_value": prompt.default_value,
                "has_default": prompt.has_default,
                "required": prompt.required_at_launch,
                "hidden": prompt.hidden,
                "allow_multiple": prompt.allow_multiple,
                "security_mode": prompt.security_mode,
                "scope_type": prompt.scope_type,
                "scope_name": prompt.scope_name,
                "source_variable_id": prompt.source_variable_id,
                "limit_type": prompt.limit_type,
                "limit_value": prompt.limit_value,
            }
            for prompt in definition.prompts
        ],
    }


def _business_rule_rtp_import_payload(result) -> dict[str, object]:
    payload = {
        "sync_run_id": result.sync_run_id,
        "source_name": result.source_name,
        "source_checksum": result.source_checksum,
        "parser_version": result.parser_version,
        "rules_imported": result.rules_imported,
        "prompts_imported": result.prompts_imported,
        "warnings": list(result.warnings),
        "completed_at": result.completed_at.isoformat(),
    }
    if result.rules_added is not None:
        payload["rules_added"] = result.rules_added
        payload["rules_changed"] = result.rules_changed
        payload["rules_unchanged"] = result.rules_unchanged
    return payload


def _business_rule_rtp_status_payload(status) -> dict[str, object]:
    return {
        "health": status.status,
        "application_name": status.application_name,
        "live_catalog_available": status.live_catalog_available,
        "live_rule_count": status.live_rule_count,
        "synchronized_rule_count": status.synchronized_rule_count,
        "synchronized_prompt_count": status.synchronized_prompt_count,
        "unsynchronized_live_rules": list(status.unsynchronized_live_rules),
        "definitions_not_in_live_catalog": list(
            status.definitions_not_in_live_catalog
        ),
        "definitions": [
            {
                "rule_name": item.rule_name,
                "cube_name": item.cube_name,
                "source_name": item.source_name,
                "synchronized_at": item.synchronized_at.isoformat(),
                "prompt_count": item.prompt_count,
                "required_prompt_count": item.required_prompt_count,
                "live_status": item.live_status,
            }
            for item in status.definitions
        ],
        "recent_syncs": [
            {
                "sync_run_id": item.sync_run_id,
                "source_name": item.source_name,
                "parser_version": item.parser_version,
                "status": item.status,
                "rules_imported": item.rules_imported,
                "prompts_imported": item.prompts_imported,
                "warnings": list(item.warnings),
                "error_summary": item.error_summary,
                "started_at": item.started_at.isoformat(),
                "completed_at": (
                    item.completed_at.isoformat()
                    if item.completed_at is not None
                    else None
                ),
            }
            for item in status.recent_syncs
        ],
    }


def _require_live_artifact(
    requested_name: str,
    available_names: tuple[str, ...],
    *,
    label: str,
) -> str:
    requested = str(requested_name).strip()
    match = next(
        (
            name
            for name in available_names
            if name.casefold() == requested.casefold()
        ),
        None,
    )
    if match is None:
        raise ConfigurationError(
            f"{label} '{requested}' is not available in the connected "
            "Planning application. Refresh the catalog and select a current "
            f"{label}."
        )
    return match


def _require_oracle_catalog(catalog) -> None:
    if not catalog.oracle_available:
        raise ConfigurationError(
            catalog.oracle_message
            or "Oracle Planning is currently unavailable."
        )


def _operation_start_error(exc: EPMError) -> JSONResponse:
    status_code = 409 if "active execution" in str(exc) else 400
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "error",
            "message": "Oracle operation could not be started.",
            "details": str(exc),
        },
    )


def _schedule_environment(request: Request) -> OracleEnvironment:
    settings = request.app.state.settings
    return OracleEnvironment.from_settings(
        settings.epm_base_url,
        settings.application_name,
    )


def _schedule_payload(schedule: AutomationSchedule) -> dict[str, object]:
    """Return a JSON-safe schedule representation."""
    variables = schedule.configuration.get("variables", {})
    inbox_files = schedule.configuration.get("inbox_files", {})
    return {
        "schedule_id": schedule.schedule_id,
        "name": schedule.name,
        "target_type": schedule.target_type.value,
        "target_key": schedule.target_key,
        "frequency": schedule.frequency.value,
        "timezone": schedule.timezone,
        "first_run_local": schedule.first_run_local.isoformat(),
        "input_policy": schedule.input_policy.value,
        "variables": variables if isinstance(variables, dict) else {},
        "inbox_files": inbox_files if isinstance(inbox_files, dict) else {},
        "misfire_policy": schedule.misfire_policy.value,
        "concurrency_policy": schedule.concurrency_policy.value,
        "enabled": schedule.enabled,
        "next_run_at": (
            schedule.next_run_at.isoformat()
            if schedule.next_run_at
            else None
        ),
        "created_at": schedule.created_at.isoformat(),
        "updated_at": schedule.updated_at.isoformat(),
        "last_triggered_at": (
            schedule.last_triggered_at.isoformat()
            if schedule.last_triggered_at
            else None
        ),
        "last_execution_id": schedule.last_execution_id,
        "last_outcome": schedule.last_outcome.value,
        "last_error": schedule.last_error,
    }


def _schedule_run_payload(run) -> dict[str, object]:
    return {
        "run_id": run.run_id,
        "schedule_id": run.schedule_id,
        "scheduled_for": run.scheduled_for.isoformat(),
        "claimed_at": run.claimed_at.isoformat(),
        "status": run.status.value,
        "completed_at": (
            run.completed_at.isoformat() if run.completed_at else None
        ),
        "execution_id": run.execution_id,
        "error_message": run.error_message,
    }


def _schedule_evidence_payload(evidence) -> dict[str, object]:
    payload = _schedule_run_payload(evidence.run)
    payload.update(
        {
            "schedule_name": evidence.schedule_name,
            "target_type": evidence.target_type.value,
            "target_key": evidence.target_key,
        }
    )
    return payload


def _schedule_error(message: str, exc: EPMError) -> JSONResponse:
    """Return a consistent schedule configuration error."""
    return JSONResponse(
        status_code=400,
        content={
            "status": "error",
            "message": message,
            "details": str(exc),
        },
    )


def _operation_accepted(
    execution_id: str,
    *,
    planning_task_id: int | None = None,
) -> JSONResponse:
    redirect = f"/app/operations/runs/{execution_id}"
    if planning_task_id is not None:
        redirect = f"{redirect}?planning_task_id={planning_task_id}"
    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "execution_id": execution_id,
            "redirect": redirect,
        },
    )


def _operation_execution_payload(
    manager,
    execution_id: str,
    settings: Settings,
):
    managed = manager.get(execution_id)
    workflow = manager.get_workflow(execution_id)
    if managed is None and workflow is None:
        return None
    if workflow is not None:
        steps = [
            {
                "name": step.name,
                "sequence": step.sequence,
                "status": step.status.value,
                "started_at": (
                    step.started_at.isoformat() if step.started_at else None
                ),
                "completed_at": (
                    step.completed_at.isoformat()
                    if step.completed_at
                    else None
                ),
                "details": step.details,
                "error_message": step.error_message,
            }
            for step in workflow.steps
        ]
        status = workflow.status.value
        operation_name = workflow.workflow_name
        started_at = workflow.started_at.isoformat()
        completed_at = (
            workflow.completed_at.isoformat()
            if workflow.completed_at
            else None
        )
        error_message = workflow.error_message
        initiated_by = workflow.initiated_by_display or workflow.initiated_by
        trigger_source = workflow.trigger_source.value
    else:
        steps = []
        status = managed.status.value
        operation_name = (
            f"{managed.operation_kind.value} · {managed.target_name}"
        )
        started_at = managed.submitted_at.isoformat()
        completed_at = None
        error_message = managed.error_message
        initiated_by = None
        trigger_source = None
    completed_steps = sum(
        item["status"]
        in {
            WorkflowStepStatus.SUCCESS.value,
            WorkflowStepStatus.FAILED.value,
            WorkflowStepStatus.SKIPPED.value,
        }
        for item in steps
    )
    artifacts: list[dict[str, str]] = []
    report_root = settings.report_output_dir.resolve()
    for step in steps:
        raw_output = step["details"].get("output_file")
        if not raw_output:
            continue
        path = (report_root / Path(str(raw_output)).name).resolve()
        if (
            path.parent == report_root
            and path.suffix.casefold() == ".xlsx"
            and path.is_file()
        ):
            artifacts.append(
                {
                    "name": path.name,
                    "url": f"/app/reports/{quote(path.name)}",
                }
            )
    flow_progress = (
        _standalone_flow_progress(manager, workflow, steps)
        if workflow is not None
        else None
    )
    record_statistics = aggregate_record_statistics(
        step["details"] for step in steps
    )
    if flow_progress is not None:
        record_statistics = flow_progress["record_statistics"]
    return {
        "execution_id": execution_id,
        "operation_name": operation_name,
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "error_message": error_message,
        "initiated_by": initiated_by,
        "trigger_source": trigger_source,
        "steps": steps,
        "completed_steps": completed_steps,
        "total_steps": len(steps),
        "artifacts": artifacts,
        "record_statistics": record_statistics,
        "flow_progress": flow_progress,
        "log_url": (
            f"/app/operations/runs/{execution_id}/log"
            if managed is not None and managed.log_file.is_file()
            else None
        ),
        "terminal": status in {"SUCCESS", "FAILED", "RECOVERY_REQUIRED"},
    }


def _standalone_flow_progress(manager, workflow, steps):
    """Project a parent flow and its child runs into one live timeline."""
    if not workflow.workflow_name.startswith("Standalone Flow - "):
        return None

    projected: list[dict[str, object]] = []
    statistic_sources: list[dict[str, object]] = []
    for step in steps:
        details = step["details"]
        child_execution_id = str(
            details.get("child_execution_id") or ""
        ).strip()
        child = (
            manager.get_workflow(child_execution_id)
            if child_execution_id
            else None
        )
        child_steps = tuple(child.steps) if child is not None else ()
        active_child_step = next(
            (
                item
                for item in child_steps
                if item.status is WorkflowStepStatus.RUNNING
            ),
            None,
        )
        oracle_evidence = next(
            (
                item.details
                for item in reversed(child_steps)
                if item.details.get("job_id") is not None
            ),
            None,
        )
        child_statistics = aggregate_record_statistics(
            item.details for item in child_steps
        )
        if child_statistics is None:
            value = details.get("record_statistics")
            if isinstance(value, dict):
                child_statistics = value
        if child_statistics is not None:
            statistic_sources.append(
                {"record_statistics": child_statistics}
            )

        operation_code = str(details.get("operation_code") or "").strip()
        display_name = str(details.get("display_name") or "").strip()
        artifact_name = str(details.get("artifact_name") or "").strip()
        if not display_name or not artifact_name:
            legacy_name = str(step["name"])
            without_sequence = legacy_name.split(". ", 1)[-1]
            name_parts = without_sequence.split(" - ", 1)
            display_name = display_name or name_parts[0]
            artifact_name = artifact_name or (
                name_parts[1] if len(name_parts) > 1 else ""
            )
        projected.append(
            {
                "sequence": step["sequence"],
                "operation_code": operation_code,
                "display_name": display_name,
                "artifact_name": artifact_name,
                "status": step["status"],
                "started_at": step["started_at"],
                "completed_at": step["completed_at"],
                "child_execution_id": child_execution_id or None,
                "child_status": (
                    child.status.value if child is not None else None
                ),
                "active_stage": (
                    active_child_step.name
                    if active_child_step is not None
                    else None
                ),
                "oracle_job_id": (
                    oracle_evidence.get("job_id")
                    if oracle_evidence is not None
                    else details.get("job_id")
                ),
                "oracle_status": (
                    oracle_evidence.get("status")
                    if oracle_evidence is not None
                    else details.get("status")
                ),
                "record_statistics": child_statistics,
                "error_message": (
                    step["error_message"]
                    or (child.error_message if child is not None else None)
                ),
            }
        )

    current = next(
        (item for item in projected if item["status"] == "RUNNING"),
        None,
    )
    if current is None and workflow.status.value in {"QUEUED", "RUNNING"}:
        current = next(
            (item for item in projected if item["status"] == "PENDING"),
            None,
        )
    terminal_count = sum(
        item["status"] in {"SUCCESS", "FAILED", "SKIPPED"}
        for item in projected
    )
    total = len(projected)
    first_details = steps[0]["details"] if steps else {}
    recovery_source = str(
        first_details.get("recovery_source_execution_id") or ""
    ).strip()
    return {
        "current_step": current,
        "completed_steps": terminal_count,
        "successful_steps": sum(
            item["status"] == "SUCCESS" for item in projected
        ),
        "total_steps": total,
        "progress_percent": (
            round(terminal_count / total * 100) if total else 0
        ),
        "steps": projected,
        "record_statistics": aggregate_record_statistics(
            statistic_sources
        ),
        "recovery": (
            {
                "source_execution_id": recovery_source,
                "from_original_step": first_details.get(
                    "recovery_from_sequence"
                ),
            }
            if recovery_source
            else None
        ),
    }


def _validate_operation_pipeline_files(
    requirements,
    *,
    upload_paths: dict[str, Path],
    inbox_files: dict[str, str],
) -> None:
    known = {
        requirement.key.casefold(): requirement
        for requirement in requirements
    }
    uploads = {
        key.casefold(): path for key, path in upload_paths.items()
    }
    inbox = {
        key.casefold(): value for key, value in inbox_files.items()
    }
    unknown = (set(uploads) | set(inbox)) - set(known)
    if unknown:
        raise ConfigurationError(
            "Pipeline file input(s) do not match the current definition: "
            + ", ".join(sorted(unknown))
        )
    duplicate = set(uploads) & set(inbox)
    if duplicate:
        raise ConfigurationError(
            "A Pipeline input cannot use both upload and Inbox sources: "
            + ", ".join(sorted(duplicate))
        )
    for key, requirement in known.items():
        path = uploads.get(key)
        if (
            requirement.required
            and path is None
            and key not in inbox
            and not requirement.configured_reference
        ):
            raise ConfigurationError(
                f"Pipeline file input '{requirement.display_name}' is "
                "required."
            )
        if (
            path is not None
            and requirement.allowed_extensions
            and path.suffix.casefold()
            not in requirement.allowed_extensions
        ):
            raise ConfigurationError(
                f"Pipeline file input '{requirement.display_name}' supports: "
                + ", ".join(requirement.allowed_extensions)
            )


def _validate_excel_pipeline_variables(
    preview,
    *,
    variables: dict[str, str],
    inbox_files: dict[str, str],
) -> None:
    """Reject ambiguous or incomplete Excel inputs before queueing work."""
    declared = {
        variable.name.casefold(): variable for variable in preview.variables
    }
    supplied = {name.casefold(): value for name, value in variables.items()}
    unknown = set(supplied) - set(declared)
    if unknown:
        raise ConfigurationError(
            "Pipeline variable(s) do not match the current definition: "
            + ", ".join(sorted(unknown))
        )
    file_values = {
        name.casefold(): value for name, value in inbox_files.items()
    }
    duplicate = set(supplied) & set(file_values)
    if duplicate:
        raise ConfigurationError(
            "Enter file variables only in the Oracle Inbox files section: "
            + ", ".join(sorted(duplicate))
        )
    for variable in preview.variables:
        value = supplied.get(variable.name.casefold())
        if value is None:
            value = file_values.get(variable.name.casefold())
        if value is None:
            value = variable.default_value
        if variable.required and not str(value or "").strip():
            raise ConfigurationError(
                f"Pipeline variable '{variable.display_name}' requires a value."
            )


def _request_actor(request: Request) -> ExecutionActor:
    user = request.state.current_user
    return ExecutionActor(
        username=user.username,
        display_name=user.display_name,
        trigger_source=TriggerSource.MANUAL,
    )


def _require_user_variable_target(user: UserAccount, target_user: str) -> None:
    """Allow ordinary users to manage only their own Oracle assignment."""
    if target_user.casefold() == user.username.casefold():
        return
    if user.has_permission(Permission.USER_MANAGE):
        return
    raise HTTPException(
        status_code=403,
        detail="Your platform role can update only your own user variables.",
    )


def _planning_task_link_callback(
    request: Request,
    planning_task_id: int | None,
    *,
    action_type: str,
) -> Callable[[str], None] | None:
    """Authorize a task now and return its pre-queue correlation callback."""
    user = _current_user(request)
    assert user is not None
    if planning_task_id is None:
        if user.has_permission(Permission.OPERATION_EXECUTE):
            return None
        raise AccessControlError(
            "Open this operation from an assigned Planning task, or ask a "
            "Power User or Service Administrator to run it."
        )
    request.app.state.planning_work.authorize_task_execution(
        planning_task_id,
        action_type=action_type,
        actor=user,
    )

    def link(execution_id: str) -> None:
        request.app.state.planning_work.link_task_execution(
            planning_task_id,
            execution_id,
            action_type=action_type,
            actor=user,
        )

    return link


def _require_operation_execution_access(
    request: Request,
    execution_id: str,
) -> None:
    """Restrict process-only users to executions linked to their own tasks."""
    user = _current_user(request)
    assert user is not None
    if user.has_permission(Permission.OPERATION_EXECUTE) or user.has_permission(
        Permission.HISTORY_VIEW
    ):
        return
    if request.app.state.planning_work.can_view_task_execution(
        execution_id,
        actor=user,
    ):
        return
    raise HTTPException(
        status_code=403,
        detail="This execution is not linked to one of your assigned tasks.",
    )


def _access_error(message: str, *, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "error",
            "message": message,
            "details": message,
        },
    )


def _data_review_error(message: str, error: EPMError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={
            "status": "error",
            "message": message,
            "details": str(error),
        },
    )


def _planning_validation_payload(validation) -> dict[str, object]:
    """Serialize compact validation evidence without exposing grid values."""
    return {
        "validation_id": validation.validation_id,
        "task_id": validation.task_id,
        "validation_type": validation.validation_type.value,
        "status": validation.status.value,
        "source_cube": validation.source_cube,
        "target_cube": validation.target_cube,
        "selection": validation.selection,
        "criteria": validation.criteria,
        "checked_cells": validation.checked_cells,
        "matched_cells": validation.matched_cells,
        "exception_count": validation.exception_count,
        "warning_count": validation.warning_count,
        "performed_by_user_id": validation.performed_by_user_id,
        "performed_at": validation.performed_at.isoformat(),
        "warning_acknowledged_by_user_id": (
            validation.warning_acknowledged_by_user_id
        ),
        "warning_acknowledged_at": (
            validation.warning_acknowledged_at.isoformat()
            if validation.warning_acknowledged_at
            else None
        ),
        "completion_allowed": validation.completion_allowed,
    }


def _excel_response(content: bytes, filename: str) -> Response:
    return Response(
        content=content,
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


def _agent_tool_activity_payload(
    activity: AgentToolActivity,
) -> dict[str, object]:
    """Expose structured read-only review results without leaking other tools."""
    payload: dict[str, object] = {
        "name": activity.name,
        "arguments": activity.arguments,
        "status": activity.status,
        "summary": activity.summary,
    }
    if (
        activity.name in {
            "list_planning_cubes",
            "list_cube_dimensions",
            "search_dimension_members",
            "review_data_slice",
            "compare_data_slices",
            "plan_multi_step_request",
        }
        and isinstance(activity.result, dict)
    ):
        payload["result"] = activity.result
    return payload


def _agent_error(error: AgentError) -> JSONResponse:
    """Return provider-safe agent errors without leaking SDK internals."""
    return JSONResponse(
        status_code=400,
        content={
            "status": "error",
            "message": "The EPM Assistant could not complete the request.",
            "details": str(error),
        },
    )


def _user_payload(user: UserAccount) -> dict[str, object]:
    """Return a password-free platform user representation."""
    return {
        "user_id": user.user_id,
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "active": user.active,
        "roles": [role.value for role in user.roles],
        "permissions": sorted(item.value for item in user.permissions),
        "created_at": user.created_at.isoformat(),
        "updated_at": user.updated_at.isoformat(),
        "last_login_at": (
            user.last_login_at.isoformat() if user.last_login_at else None
        ),
    }


def _role_payload(role: RoleDefinition) -> dict[str, object]:
    return {
        "code": role.code.value,
        "name": role.name,
        "description": role.description,
        "permissions": sorted(item.value for item in role.permissions),
    }

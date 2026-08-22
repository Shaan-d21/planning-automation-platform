"""Versioned session and frontend-bootstrap endpoints."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.api.v1.schemas import (
    PlanningCycleCreateRequest,
    PlanningApprovalDecisionRequest,
    PlanningTaskStatusRequest,
    PlatformPasswordChangeRequest,
    IdentitySynchronizationRequest,
    IdentityAuthenticationSummary,
    InitialAdministratorRequest,
    IdentityProvisioningRequest,
    IdentityRoleMappingRequest,
    PlatformUserCreateRequest,
    PlatformUserEditRequest,
    CurrentUserSummary,
    EnvironmentSummary,
    FeatureAvailability,
    FrontendBootstrapResponse,
    NavigationItem,
    OperationSummary,
    OperationsResponse,
    ProductSummary,
    SessionLoginRequest,
    SessionResponse,
)
from app.models.planning_workflow import (
    CycleStageDraft,
    PlanningCycleDraft,
    PlanningTaskDraft,
    PlanningTaskPriority,
    PlanningTaskStatus,
)
from app.models.planning_governance import PlanningApprovalStatus
from app.utils.exceptions import (
    AccessControlError,
    EPMError,
    IdentitySnapshotChangedError,
    IdentitySynchronizationError,
    PlanningWorkflowError,
)
from app.application.identity_access import (
    identity_preview_payload,
    provisioning_preview_payload,
)
from app.models.access_control import Permission, UserAccount
from app.web.security import (
    client_ip,
    csrf_token,
    current_user,
    require_api_session,
    start_user_session,
    validate_csrf,
)
from app.application.execution_evidence import aggregate_record_statistics


router = APIRouter(prefix="/api/v1", tags=["frontend-v1"])


@dataclass(frozen=True, slots=True)
class _NavigationDefinition:
    code: str
    label: str
    path: str
    group: str
    permissions: tuple[Permission, ...] = ()


_NAVIGATION = (
    _NavigationDefinition("home", "Home", "#home", "workspace"),
    _NavigationDefinition("tasks", "My Work", "#tasks", "workspace"),
    _NavigationDefinition("notifications", "Notifications", "#notifications", "workspace"),
    _NavigationDefinition(
        "approvals",
        "Approvals",
        "#approvals",
        "planning",
        (Permission.PROCESS_RUN,),
    ),
    _NavigationDefinition(
        "data-review",
        "Data Review",
        "#data-review",
        "planning",
        (Permission.DATA_REVIEW,),
    ),
    _NavigationDefinition(
        "operations",
        "Operations",
        "#operations",
        "automation",
        (Permission.OPERATION_EXECUTE, Permission.USER_VARIABLE_UPDATE),
    ),
    _NavigationDefinition(
        "schedules",
        "Schedules",
        "#schedules",
        "automation",
        (Permission.SCHEDULE_MANAGE,),
    ),
    _NavigationDefinition(
        "reports",
        "Reports",
        "#reports",
        "analysis",
        (Permission.REPORT_GENERATE,),
    ),
    _NavigationDefinition(
        "jobs",
        "Jobs & Activity",
        "#jobs",
        "analysis",
        (Permission.HISTORY_VIEW,),
    ),
    _NavigationDefinition(
        "assistant",
        "EPM Assistant",
        "#assistant",
        "workspace",
        (Permission.AGENT_USE,),
    ),
    _NavigationDefinition(
        "cycles",
        "Planning Cycles",
        "#cycles",
        "administration",
        (Permission.PROCESS_DESIGN,),
    ),
    _NavigationDefinition(
        "access-control",
        "Access Control",
        "#access",
        "administration",
        (Permission.USER_MANAGE,),
    ),
)


@router.get("/bootstrap", response_model=FrontendBootstrapResponse)
async def frontend_bootstrap(request: Request) -> FrontendBootstrapResponse:
    """Return safe product, session, and authorization bootstrap state."""
    settings = request.app.state.settings
    user = current_user(request)
    authenticated = user is not None and bool(
        str(request.session.get("session_id", "")).strip()
    )
    return FrontendBootstrapResponse(
        product=ProductSummary(
            name="Oracle EPM Automation Platform",
            company="BISP Solutions",
            api_version="v1",
        ),
        authenticated=authenticated,
        requires_bootstrap=request.app.state.access_control.requires_bootstrap,
        csrf_token=csrf_token(request),
        identity_authentication=IdentityAuthenticationSummary(
            federated_enabled=settings.federated_identity_ready,
            provider_name="Oracle Cloud Identity",
            login_url=(
                "/auth/oracle/start"
                if settings.federated_identity_ready
                else None
            ),
            local_recovery_enabled=True,
        ),
        environment=(
            EnvironmentSummary(
                application_name=settings.application_name,
                deployment_mode=settings.deployment_mode,
            )
            if authenticated
            else None
        ),
        user=_user_summary(user) if authenticated and user else None,
        navigation=_navigation_for(user) if authenticated and user else [],
        features=FeatureAvailability(),
    )


@router.post("/session", response_model=SessionResponse)
async def create_session(
    request: Request,
    payload: SessionLoginRequest,
) -> SessionResponse:
    """Authenticate one platform user and rotate the signed session."""
    validate_csrf(request)
    user = await run_in_threadpool(
        request.app.state.access_control.authenticate,
        payload.username,
        payload.password,
        ip_address=client_ip(request),
    )
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="The username or password is incorrect.",
        )
    start_user_session(request, user)
    return SessionResponse(
        status="success",
        message="Signed in successfully.",
        csrf_token=csrf_token(request),
        user=_user_summary(user),
    )


@router.post("/access-control/bootstrap", response_model=SessionResponse)
async def bootstrap_administrator(
    request: Request,
    payload: InitialAdministratorRequest,
) -> SessionResponse:
    """Create the first administrator and start its signed session."""
    validate_csrf(request)
    try:
        user = await run_in_threadpool(
            request.app.state.access_control.bootstrap_administrator,
            username=payload.username.strip(),
            display_name=payload.display_name.strip(),
            email=payload.email.strip() if payload.email else None,
            password=payload.password,
            ip_address=client_ip(request),
        )
    except AccessControlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    start_user_session(request, user)
    return SessionResponse(
        status="success",
        message="Platform Administrator created.",
        csrf_token=csrf_token(request),
        user=_user_summary(user),
    )


@router.delete("/session", response_model=SessionResponse)
async def delete_session(request: Request) -> SessionResponse:
    """End the authenticated session and remove its temporary uploads."""
    owner = require_api_session(request)
    user = current_user(request)
    request.app.state.upload_store.delete_owner(owner)
    if user is not None:
        request.app.state.access_control.record_logout(
            user,
            ip_address=client_ip(request),
        )
    request.session.clear()
    return SessionResponse(
        status="success",
        message="Signed out successfully.",
    )


@router.get("/home")
async def planning_home(request: Request) -> dict[str, object]:
    """Return user-specific priorities and active business-cycle progress."""
    require_api_session(request)
    user = current_user(request)
    assert user is not None
    snapshot = request.app.state.planning_work.home_snapshot(actor=user)
    visible_tasks = snapshot.tasks[:50]
    attempts = request.app.state.planning_work.execution_attempts(visible_tasks)
    validations = request.app.state.planning_work.validation_evidence(visible_tasks)
    return {
        "status": "success",
        "summary": {
            "action_required": snapshot.action_required,
            "due_today": snapshot.due_today,
            "overdue": snapshot.overdue,
            "completed": snapshot.completed,
        },
        "cycles": [_overview_payload(item) for item in snapshot.cycles],
        "tasks": [
            _task_payload(
                item,
                attempts.get(item.task_id, ()),
                validations.get(item.task_id),
            )
            for item in visible_tasks
        ],
        "recent_activity": [
            {
                "execution_id": item.execution_id,
                "name": item.name,
                "status": item.status,
                "started_at": item.started_at.isoformat(),
                "initiated_by": item.initiated_by,
                "trigger_source": item.trigger_source,
            }
            for item in snapshot.recent_activity
        ],
    }


@router.get("/operations", response_model=OperationsResponse)
async def standalone_operations(request: Request) -> OperationsResponse:
    """Return standalone services that the current user may launch."""
    require_api_session(request)
    user = current_user(request)
    assert user is not None
    operations = []
    for operation in request.app.state.operation_catalog.definitions():
        required_permission = _operation_permission(operation.code)
        if not user.has_permission(required_permission):
            continue
        operations.append(
            OperationSummary(
                code=operation.code,
                display_name=operation.display_name,
                description=operation.description,
                category=operation.category,
                risk_level=operation.risk_level,
                route=operation.route,
            )
        )
    return OperationsResponse(status="success", operations=operations)


@router.get("/planning-cycles")
async def list_planning_cycles(request: Request) -> dict[str, object]:
    """List active operational cycles without exposing technical definitions."""
    require_api_session(request)
    user = current_user(request)
    assert user is not None
    cycles = request.app.state.planning_work.list_cycles(actor=user)
    return {
        "status": "success",
        "cycles": [_cycle_payload(item) for item in cycles],
    }


@router.get("/planning-cycle-administration")
async def planning_cycle_administration(
    request: Request,
) -> dict[str, object]:
    """Return safe cycle, assignee, and role choices for cycle designers."""
    require_api_session(request)
    user = current_user(request)
    assert user is not None
    try:
        cycles = request.app.state.planning_work.administration_workspace(
            actor=user,
        )
    except PlanningWorkflowError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    users = tuple(
        account
        for account in request.app.state.access_control.list_users()
        if account.active
    )
    return {
        "status": "success",
        "cycles": [_overview_payload(item) for item in cycles],
        "users": [
            {
                "user_id": account.user_id,
                "username": account.username,
                "display_name": account.display_name,
                "roles": [role.value for role in account.roles],
            }
            for account in users
        ],
        "roles": [
            {
                "code": role.code.value,
                "name": role.name,
                "description": role.description,
            }
            for role in request.app.state.access_control.roles()
        ],
    }


@router.get("/planning-tasks")
async def assigned_planning_tasks(
    request: Request,
    cycle_id: int | None = None,
) -> dict[str, object]:
    """Return complete assigned work and cycle context for the task workspace."""
    require_api_session(request)
    user = current_user(request)
    assert user is not None
    cycles, tasks = request.app.state.planning_work.assigned_work(
        actor=user,
        cycle_id=cycle_id,
    )
    attempts = request.app.state.planning_work.execution_attempts(tasks)
    validations = request.app.state.planning_work.validation_evidence(tasks)
    return {
        "status": "success",
        "cycles": [_overview_payload(item) for item in cycles],
        "tasks": [
            _task_payload(
                item,
                attempts.get(item.task_id, ()),
                validations.get(item.task_id),
            )
            for item in tasks
        ],
    }


@router.get("/planning-cycles/{cycle_id}")
async def planning_cycle_detail(
    request: Request,
    cycle_id: int,
) -> dict[str, object]:
    """Return stage progress and only the tasks visible to the current user."""
    require_api_session(request)
    user = current_user(request)
    assert user is not None
    try:
        cycle, stages, tasks = request.app.state.planning_work.cycle_detail(
            cycle_id,
            actor=user,
        )
    except PlanningWorkflowError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    attempts = request.app.state.planning_work.execution_attempts(tasks)
    validations = request.app.state.planning_work.validation_evidence(tasks)
    return {
        "status": "success",
        "cycle": _cycle_payload(cycle),
        "stages": [_stage_payload(item) for item in stages],
        "tasks": [
            _task_payload(
                item,
                attempts.get(item.task_id, ()),
                validations.get(item.task_id),
            )
            for item in tasks
        ],
    }


@router.post("/planning-cycles", status_code=201)
async def create_planning_cycle(
    request: Request,
    payload: PlanningCycleCreateRequest,
) -> dict[str, object]:
    """Create a complete operational cycle aggregate atomically."""
    require_api_session(request)
    user = current_user(request)
    assert user is not None
    try:
        tasks = tuple(
            PlanningTaskDraft(
                key=item.key,
                stage_code=item.stage_code,
                title=item.title,
                description=item.description,
                task_type=item.task_type,
                priority=PlanningTaskPriority(item.priority.strip().upper()),
                assigned_username=item.assigned_username,
                assigned_role_code=item.assigned_role_code,
                entity=item.entity,
                scenario=item.scenario,
                period=item.period,
                due_at=item.due_at,
                action_type=item.action_type,
                action_config=item.action_config,
                depends_on=tuple(item.depends_on),
            )
            for item in payload.tasks
        )
        cycle = request.app.state.planning_work.create_cycle(
            PlanningCycleDraft(
                code=payload.code,
                name=payload.name,
                cycle_type=payload.cycle_type,
                process_code=payload.process_code,
                scenario=payload.scenario,
                year=payload.year,
                actual_through_period=payload.actual_through_period,
                forecast_start_period=payload.forecast_start_period,
                start_date=payload.start_date,
                due_date=payload.due_date,
                stages=tuple(
                    CycleStageDraft(
                        code=item.code,
                        name=item.name,
                        sequence=item.sequence,
                        start_date=item.start_date,
                        due_date=item.due_date,
                    )
                    for item in payload.stages
                ),
                tasks=tasks,
            ),
            actor=user,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="Task priority must be LOW, NORMAL, HIGH, or CRITICAL.",
        ) from exc
    except PlanningWorkflowError as exc:
        status_code = 403 if "does not permit" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return {
        "status": "success",
        "cycle": _cycle_payload(cycle),
    }


@router.patch("/planning-tasks/{task_id}/status")
async def update_planning_task_status(
    request: Request,
    task_id: int,
    payload: PlanningTaskStatusRequest,
) -> dict[str, object]:
    """Apply one assignee-authorized task transition."""
    require_api_session(request)
    user = current_user(request)
    assert user is not None
    try:
        status = PlanningTaskStatus(payload.status.strip().upper())
        task = request.app.state.planning_work.update_task_status(
            task_id,
            status,
            actor=user,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="Unsupported Planning task status.",
        ) from exc
    except PlanningWorkflowError as exc:
        message = str(exc)
        status_code = (
            403
            if "not assigned" in message or "Only a Planning" in message
            else 400
        )
        raise HTTPException(status_code=status_code, detail=message) from exc
    return {
        "status": "success",
        "task": _task_payload(task),
    }


@router.get("/planning-tasks/{task_id}/data-review-context")
async def planning_task_data_review_context(
    request: Request,
    task_id: int,
) -> dict[str, object]:
    """Return a governed, reusable Data Review layout for one assignment."""
    require_api_session(request)
    user = current_user(request)
    assert user is not None
    try:
        task, evidence = request.app.state.planning_work.data_review_context(
            task_id,
            actor=user,
        )
    except PlanningWorkflowError as exc:
        message = str(exc)
        raise HTTPException(
            status_code=403 if "not assigned" in message else 400,
            detail=message,
        ) from exc
    configured = task.action_config.get("data_review")
    return {
        "status": "success",
        "task": _task_payload(task, validation=evidence),
        "configuration": configured if isinstance(configured, dict) else None,
        "suggested_slice": (
            configured.get("slice")
            if isinstance(configured, dict)
            and isinstance(configured.get("slice"), dict)
            else evidence.selection if evidence else None
        ),
    }


@router.post("/planning-validations/{validation_id}/acknowledge")
async def acknowledge_planning_validation(
    request: Request,
    validation_id: int,
) -> dict[str, object]:
    """Explicitly acknowledge the latest warning-only validation result."""
    require_api_session(request)
    user = current_user(request)
    assert user is not None
    try:
        evidence = request.app.state.planning_work.acknowledge_validation_warning(
            validation_id,
            actor=user,
        )
    except PlanningWorkflowError as exc:
        message = str(exc)
        raise HTTPException(
            status_code=403 if "not assigned" in message else 400,
            detail=message,
        ) from exc
    return {"status": "success", "validation": _validation_payload(evidence)}


@router.post("/planning-tasks/{task_id}/submit-for-approval")
async def submit_planning_task_for_approval(
    request: Request,
    task_id: int,
) -> dict[str, object]:
    """Submit completed Planning work to its configured reviewer."""
    require_api_session(request)
    user = current_user(request)
    assert user is not None
    try:
        approvals = request.app.state.planning_work.submit_for_approval(
            task_id,
            actor=user,
        )
    except PlanningWorkflowError as exc:
        message = str(exc)
        status_code = 403 if "not assigned" in message else 400
        raise HTTPException(status_code=status_code, detail=message) from exc
    return {
        "status": "success",
        "approvals": [_approval_payload(item) for item in approvals],
    }


@router.get("/planning-approvals")
async def list_planning_approvals(request: Request) -> dict[str, object]:
    """Return review requests assigned to the signed-in manager."""
    require_api_session(request)
    user = current_user(request)
    assert user is not None
    approvals = request.app.state.planning_work.approvals(actor=user)
    return {
        "status": "success",
        "approvals": [_approval_payload(item) for item in approvals],
    }


@router.patch("/planning-approvals/{approval_id}")
async def decide_planning_approval(
    request: Request,
    approval_id: int,
    payload: PlanningApprovalDecisionRequest,
) -> dict[str, object]:
    """Approve or return one governed Planning submission."""
    require_api_session(request)
    user = current_user(request)
    assert user is not None
    try:
        decision = PlanningApprovalStatus(payload.decision.strip().upper())
        approval = request.app.state.planning_work.decide_approval(
            approval_id,
            decision,
            comment=payload.comment,
            actor=user,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="Decision must be APPROVED or RETURNED.",
        ) from exc
    except PlanningWorkflowError as exc:
        message = str(exc)
        status_code = 403 if "not assigned" in message else 400
        raise HTTPException(status_code=status_code, detail=message) from exc
    return {"status": "success", "approval": _approval_payload(approval)}


@router.get("/notifications")
async def list_user_notifications(request: Request) -> dict[str, object]:
    """Return durable notifications owned by the signed-in user."""
    require_api_session(request)
    user = current_user(request)
    assert user is not None
    notifications = request.app.state.planning_work.notifications(actor=user)
    return {
        "status": "success",
        "unread_count": sum(1 for item in notifications if item.read_at is None),
        "notifications": [_notification_payload(item) for item in notifications],
    }


@router.patch("/notifications/{notification_id}/read")
async def read_user_notification(
    request: Request,
    notification_id: int,
) -> dict[str, object]:
    require_api_session(request)
    user = current_user(request)
    assert user is not None
    try:
        notification = request.app.state.planning_work.read_notification(
            notification_id,
            actor=user,
        )
    except PlanningWorkflowError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "status": "success",
        "notification": _notification_payload(notification),
    }


@router.post("/notifications/read-all")
async def read_all_user_notifications(request: Request) -> dict[str, str]:
    require_api_session(request)
    user = current_user(request)
    assert user is not None
    request.app.state.planning_work.read_all_notifications(actor=user)
    return {"status": "success"}


@router.get("/jobs")
async def list_jobs_activity(
    request: Request,
    limit: int = 100,
) -> dict[str, object]:
    """Return retained execution summaries for authorized operators."""
    require_api_session(request)
    bounded_limit = min(max(limit, 1), 250)
    runs = request.app.state.control_center.list_workflow_runs(
        limit=bounded_limit,
    )
    terminal = [
        run for run in runs if run.status.value in {"SUCCESS", "FAILED"}
    ]
    successful = sum(run.status.value == "SUCCESS" for run in terminal)
    return {
        "status": "success",
        "summary": {
            "total": len(runs),
            "running": sum(
                run.status.value in {"QUEUED", "RUNNING"} for run in runs
            ),
            "successful": successful,
            "failed": sum(run.status.value == "FAILED" for run in runs),
            "success_rate": (
                round(successful / len(terminal) * 100) if terminal else 0
            ),
        },
        "jobs": [_job_summary_payload(run) for run in runs],
    }


@router.get("/jobs/{execution_id}")
async def job_activity_detail(
    request: Request,
    execution_id: str,
) -> dict[str, object]:
    """Return step-level evidence for one retained execution."""
    require_api_session(request)
    run = request.app.state.control_center.get_workflow_run(execution_id)
    if run is None:
        raise HTTPException(status_code=404, detail="The requested job was not found.")
    return {"status": "success", "job": _job_detail_payload(run)}


@router.get("/access-control")
async def access_control_workspace(request: Request) -> dict[str, object]:
    """Return the administrator's password-free user and role catalog."""
    user = _require_user_management(request)
    return {
        "status": "success",
        "current_user_id": user.user_id,
        "users": [
            _platform_user_payload(account)
            for account in request.app.state.access_control.list_users()
        ],
        "roles": [
            _platform_role_payload(role)
            for role in request.app.state.access_control.roles()
        ],
        "identity_sync": request.app.state.identity_access.status(),
    }


@router.post("/access-control/identity-sync/preview")
async def preview_identity_synchronization(
    request: Request,
) -> dict[str, object]:
    """Inspect live Oracle users, roles, and groups without persisting changes."""
    _require_user_management(request)
    validate_csrf(request)
    try:
        preview = await run_in_threadpool(request.app.state.identity_access.preview)
    except IdentitySynchronizationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except EPMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"status": "success", "preview": identity_preview_payload(preview)}


@router.post("/access-control/identity-sync/apply")
async def apply_identity_synchronization(
    request: Request,
    payload: IdentitySynchronizationRequest,
) -> dict[str, object]:
    """Persist a reviewed Oracle snapshot without assigning platform roles."""
    actor = _require_user_management(request)
    validate_csrf(request)
    try:
        result, preview = await run_in_threadpool(
            request.app.state.identity_access.synchronize,
            payload.snapshot_checksum,
            initiated_by_user_id=actor.user_id,
        )
    except IdentitySnapshotChangedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IdentitySynchronizationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except EPMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "status": "success",
        "message": (
            "Oracle identities and entitlements were synchronized. Platform "
            "roles and sign-in behavior were not changed."
        ),
        "result": {
            "sync_run_id": result.sync_run_id,
            "sync_status": result.status.value,
            "identities_seen": result.identities_seen,
            "identities_linked": result.identities_linked,
            "identities_deactivated": result.identities_deactivated,
            "entitlements_seen": result.entitlements_seen,
            "completed_at": result.completed_at.isoformat(),
        },
        "applied_preview": identity_preview_payload(preview),
    }


@router.get("/access-control/identity-mappings")
async def identity_role_mapping_catalog(request: Request) -> dict[str, object]:
    """Return synchronized Oracle entitlements and current role mappings."""
    _require_user_management(request)
    try:
        catalog = request.app.state.identity_access.entitlement_catalog()
    except IdentitySynchronizationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "success", **catalog}


@router.put("/access-control/identity-mappings/{entitlement_id}")
async def set_identity_role_mapping(
    request: Request,
    entitlement_id: int,
    payload: IdentityRoleMappingRequest,
) -> dict[str, object]:
    """Create or replace one explicit Oracle-to-platform role mapping."""
    actor = _require_user_management(request)
    validate_csrf(request)
    try:
        mapping = request.app.state.identity_access.set_mapping(
            entitlement_id,
            payload.role_code,
            actor_user_id=actor.user_id,
        )
    except IdentitySynchronizationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "success", "mapping": mapping}


@router.delete("/access-control/identity-mappings/{entitlement_id}")
async def remove_identity_role_mapping(
    request: Request,
    entitlement_id: int,
) -> dict[str, str]:
    """Remove a mapping; account changes still require a separate preview."""
    _require_user_management(request)
    validate_csrf(request)
    try:
        request.app.state.identity_access.remove_mapping(entitlement_id)
    except IdentitySynchronizationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "success", "message": "Role mapping removed."}


@router.post("/access-control/identity-provisioning/preview")
async def preview_identity_provisioning(
    request: Request,
) -> dict[str, object]:
    """Preview collision-safe passwordless platform account changes."""
    _require_user_management(request)
    validate_csrf(request)
    try:
        preview = request.app.state.identity_access.provisioning_preview()
    except IdentitySynchronizationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "success",
        "preview": provisioning_preview_payload(preview),
    }


@router.post("/access-control/identity-provisioning/apply")
async def apply_identity_provisioning(
    request: Request,
    payload: IdentityProvisioningRequest,
) -> dict[str, object]:
    """Apply the reviewed safe entries while retaining collision evidence."""
    actor = _require_user_management(request)
    validate_csrf(request)
    try:
        result, preview = request.app.state.identity_access.provision(
            payload.provisioning_checksum,
            actor_user_id=actor.user_id,
        )
    except IdentitySnapshotChangedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IdentitySynchronizationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "success",
        "message": (
            f"Provisioning completed: {result.created} created, "
            f"{result.updated} updated, and {result.deactivated} deactivated."
        ),
        "result": {
            "created": result.created,
            "updated": result.updated,
            "deactivated": result.deactivated,
            "unchanged": result.unchanged,
            "unmapped": result.unmapped,
            "conflicts": result.conflicts,
        },
        "applied_preview": provisioning_preview_payload(preview),
    }


@router.post("/access-control/users", status_code=201)
async def create_access_control_user(
    request: Request,
    payload: PlatformUserCreateRequest,
) -> dict[str, object]:
    """Create an active platform user with one primary role."""
    actor = _require_user_management(request)
    try:
        account = request.app.state.access_control.create_user(
            username=payload.username,
            display_name=payload.display_name,
            email=payload.email,
            password=payload.password,
            roles=(payload.role_code,),
            actor_user_id=actor.user_id,
        )
    except AccessControlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "success", "user": _platform_user_payload(account)}


@router.patch("/access-control/users/{user_id}")
async def update_access_control_user(
    request: Request,
    user_id: int,
    payload: PlatformUserEditRequest,
) -> dict[str, object]:
    """Update profile, activation state, and the primary platform role."""
    actor = _require_user_management(request)
    if actor.user_id == user_id and not payload.active:
        raise HTTPException(
            status_code=400,
            detail="You cannot deactivate your own signed-in account.",
        )
    try:
        account = request.app.state.access_control.update_user(
            user_id,
            display_name=payload.display_name,
            email=payload.email,
            active=payload.active,
            roles=(payload.role_code,),
            actor_user_id=actor.user_id,
        )
    except AccessControlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "success", "user": _platform_user_payload(account)}


@router.post("/access-control/users/{user_id}/password")
async def reset_access_control_password(
    request: Request,
    user_id: int,
    payload: PlatformPasswordChangeRequest,
) -> dict[str, str]:
    """Replace a platform password without returning credential material."""
    actor = _require_user_management(request)
    if payload.password != payload.password_confirmation:
        raise HTTPException(
            status_code=400,
            detail="Password confirmation does not match.",
        )
    try:
        request.app.state.access_control.reset_password(
            user_id,
            payload.password,
            actor_user_id=actor.user_id,
        )
    except AccessControlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "success", "message": "Password updated successfully."}


def _require_user_management(request: Request) -> UserAccount:
    """Resolve and authorize the signed-in Access Control administrator."""
    require_api_session(request)
    user = current_user(request)
    if user is None or not user.has_permission(Permission.USER_MANAGE):
        raise HTTPException(
            status_code=403,
            detail="Only a Service Administrator can manage platform access.",
        )
    return user


def _user_summary(user: UserAccount) -> CurrentUserSummary:
    persona, persona_label = _persona_for(user)
    return CurrentUserSummary(
        user_id=user.user_id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        platform_roles=[role.value for role in user.roles],
        permissions=sorted(permission.value for permission in user.permissions),
        persona=persona,
        persona_label=persona_label,
    )


def _platform_user_payload(user: UserAccount) -> dict[str, object]:
    """Return a credential-free representation of one platform identity."""
    return {
        "user_id": user.user_id,
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "active": user.active,
        "role_code": user.roles[0].value if user.roles else None,
        "created_at": user.created_at.isoformat(),
        "updated_at": user.updated_at.isoformat(),
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


def _platform_role_payload(role) -> dict[str, object]:
    return {
        "code": role.code.value,
        "name": role.name,
        "description": role.description,
        "permissions": sorted(permission.value for permission in role.permissions),
    }


def _job_summary_payload(run) -> dict[str, object]:
    return {
        "execution_id": run.execution_id,
        "name": run.workflow_name,
        "status": run.status.value,
        "started_at": run.started_at.isoformat(),
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "duration_seconds": _duration_seconds(run.started_at, run.completed_at),
        "completed_steps": sum(
            step.status.value in {"SUCCESS", "FAILED", "SKIPPED"}
            for step in run.steps
        ),
        "total_steps": len(run.steps),
        "initiated_by": run.initiated_by_display or run.initiated_by or "System",
        "trigger_source": run.trigger_source.value,
        "error_message": _safe_error(run.error_message),
    }


def _job_detail_payload(run) -> dict[str, object]:
    return {
        **_job_summary_payload(run),
        "record_statistics": aggregate_record_statistics(
            step.details for step in run.steps
        ),
        "steps": [
            {
                "sequence": step.sequence,
                "name": step.name,
                "status": step.status.value,
                "started_at": step.started_at.isoformat() if step.started_at else None,
                "completed_at": step.completed_at.isoformat() if step.completed_at else None,
                "duration_seconds": (
                    _duration_seconds(step.started_at, step.completed_at)
                    if step.started_at
                    else None
                ),
                "details": _safe_job_details(step.details),
                "error_message": _safe_error(step.error_message),
            }
            for step in run.steps
        ],
    }


def _duration_seconds(started_at, completed_at) -> int | None:
    if completed_at is None:
        return None
    return max(0, round((completed_at - started_at).total_seconds()))


def _safe_error(value: object) -> str | None:
    text = str(value or "").strip()
    return text[:2000] if text else None


def _safe_job_details(value):
    """Redact credential-shaped fields before returning execution evidence."""
    forbidden = {"password", "secret", "token", "api_key", "apikey", "authorization", "credential"}
    if isinstance(value, dict):
        return {
            str(key): "[redacted]"
            if any(marker in str(key).casefold() for marker in forbidden)
            else _safe_job_details(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_safe_job_details(item) for item in value]
    if isinstance(value, str):
        return value[:1000]
    return value


def _persona_for(user: UserAccount) -> tuple[str, str]:
    """Project the internal RBAC model into a business-facing experience."""
    role_codes = {role.value for role in user.roles}
    if user.has_permission(Permission.USER_MANAGE) or user.has_permission(
        Permission.PROCESS_DESIGN
    ):
        return "SERVICE_ADMINISTRATOR", "Service Administrator"
    if "POWER_USER" in role_codes:
        return "POWER_USER", "Power User / FP&A"
    if "VIEWER" in role_codes:
        return "VIEWER", "Viewer / Executive"
    if "USER" in role_codes:
        return "USER", "User / Planner"
    if user.has_permission(Permission.PROCESS_RUN):
        return "USER", "User / Planner"
    return "VIEWER", "Viewer / Executive"


def _navigation_for(user: UserAccount) -> list[NavigationItem]:
    return [
        NavigationItem(
            code=item.code,
            label=item.label,
            path=item.path,
            group=item.group,
        )
        for item in _NAVIGATION
        if not item.permissions
        or any(user.has_permission(permission) for permission in item.permissions)
    ]


def _operation_permission(code: str) -> Permission:
    """Resolve the permission enforced by an operation's destination route."""
    if code == "report-generation":
        return Permission.REPORT_GENERATE
    if code == "substitution-variables":
        return Permission.VARIABLE_UPDATE
    if code == "user-variables":
        return Permission.USER_VARIABLE_UPDATE
    return Permission.OPERATION_EXECUTE


def _cycle_payload(cycle) -> dict[str, object]:
    return {
        "cycle_id": cycle.cycle_id,
        "code": cycle.code,
        "name": cycle.name,
        "cycle_type": cycle.cycle_type,
        "process_code": cycle.process_code,
        "scenario": cycle.scenario,
        "year": cycle.year,
        "actual_through_period": cycle.actual_through_period,
        "forecast_start_period": cycle.forecast_start_period,
        "start_date": cycle.start_date.isoformat(),
        "due_date": cycle.due_date.isoformat(),
        "status": cycle.status.value,
        "completed_at": (
            cycle.completed_at.isoformat() if cycle.completed_at else None
        ),
    }


def _stage_payload(stage) -> dict[str, object]:
    return {
        "stage_id": stage.stage_id,
        "cycle_id": stage.cycle_id,
        "sequence": stage.sequence,
        "code": stage.code,
        "name": stage.name,
        "status": stage.status.value,
        "start_date": stage.start_date.isoformat() if stage.start_date else None,
        "due_date": stage.due_date.isoformat() if stage.due_date else None,
        "completed_at": (
            stage.completed_at.isoformat() if stage.completed_at else None
        ),
    }


def _task_payload(task, executions=(), validation=None) -> dict[str, object]:
    return {
        "task_id": task.task_id,
        "stage_id": task.stage_id,
        "cycle_id": task.cycle_id,
        "cycle_code": task.cycle_code,
        "cycle_name": task.cycle_name,
        "stage_code": task.stage_code,
        "stage_name": task.stage_name,
        "title": task.title,
        "description": task.description,
        "task_type": task.task_type,
        "status": task.status.value,
        "readiness": task.readiness.value,
        "priority": task.priority.value,
        "assigned_user_id": task.assigned_user_id,
        "assigned_role_code": task.assigned_role_code,
        "entity": task.entity,
        "scenario": task.scenario,
        "period": task.period,
        "due_at": task.due_at.isoformat() if task.due_at else None,
        "action_type": task.action_type,
        "action_config": task.action_config,
        "dependency_ids": list(task.dependency_ids),
        "incomplete_dependency_ids": list(task.incomplete_dependency_ids),
        "completed_at": (
            task.completed_at.isoformat() if task.completed_at else None
        ),
        "execution_attempts": [
            _task_execution_payload(item) for item in executions
        ],
        "latest_validation": (
            _validation_payload(validation) if validation else None
        ),
    }


def _task_execution_payload(execution) -> dict[str, object]:
    return {
        "task_execution_id": execution.task_execution_id,
        "execution_id": execution.execution_id,
        "attempt_number": execution.attempt_number,
        "status": execution.status.value,
        "linked_at": execution.linked_at.isoformat(),
        "updated_at": execution.updated_at.isoformat(),
        "completed_at": (
            execution.completed_at.isoformat()
            if execution.completed_at
            else None
        ),
        "error_message": execution.error_message,
        "run_url": f"/app/operations/runs/{execution.execution_id}",
    }


def _approval_payload(approval) -> dict[str, object]:
    return {
        "approval_id": approval.approval_id,
        "cycle_id": approval.cycle_id,
        "cycle_name": approval.cycle_name,
        "submitted_task_id": approval.submitted_task_id,
        "submitted_task_title": approval.submitted_task_title,
        "approval_task_id": approval.approval_task_id,
        "approval_task_title": approval.approval_task_title,
        "entity": approval.entity,
        "scenario": approval.scenario,
        "period": approval.period,
        "status": approval.status.value,
        "submitted_by_user_id": approval.submitted_by_user_id,
        "submitted_by_name": approval.submitted_by_name,
        "submitted_at": approval.submitted_at.isoformat(),
        "decided_by_user_id": approval.decided_by_user_id,
        "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
        "decision_comment": approval.decision_comment,
        "validation": (
            _validation_payload(approval.validation)
            if approval.validation
            else None
        ),
    }


def _validation_payload(validation) -> dict[str, object]:
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


def _notification_payload(notification) -> dict[str, object]:
    return {
        "notification_id": notification.notification_id,
        "event_type": notification.event_type,
        "severity": notification.severity.value,
        "title": notification.title,
        "message": notification.message,
        "action_url": notification.action_url,
        "source_type": notification.source_type,
        "source_id": notification.source_id,
        "created_at": notification.created_at.isoformat(),
        "read_at": notification.read_at.isoformat() if notification.read_at else None,
    }


def _overview_payload(overview) -> dict[str, object]:
    return {
        **_cycle_payload(overview.cycle),
        "completed_stages": overview.completed_stages,
        "stage_count": len(overview.stages),
        "progress_percent": overview.progress_percent,
        "current_stage": (
            _stage_payload(overview.current_stage)
            if overview.current_stage
            else None
        ),
        "stages": [_stage_payload(item) for item in overview.stages],
    }

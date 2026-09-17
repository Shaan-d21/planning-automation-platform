"""Tests for the FastAPI enterprise control center."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, Mock, call

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.agent.models import AgentMessageRole, AgentToolActivity
from app.agent.repository import SQLiteAgentRepository
from app.application.connection import ConnectionResult
from app.models.environment import ApplicationInfo
from app.models.environment_configuration import EnvironmentConfiguration
from app.application.data_review import (
    DataReviewComparison,
    DataReviewCube,
    DataReviewGrid,
    DataReviewMemberSearch,
    DataReviewQualityValidation,
)
from app.application.operations import (
    OPERATION_DEFINITIONS,
    OperationCatalog,
    OperationKind,
    OracleArtifactSyncResult,
    PipelineOperationPreview,
    PipelineStagePreview,
    PipelineVariablePreview,
)
from app.application.oracle_files import (
    OracleFileCatalog,
    OracleFilePurpose,
)
from app.application.operation_execution_manager import OperationExecutionStatus
from app.application.standalone_flow_recovery import (
    RecoveryStepPreview,
    StandaloneFlowRecoveryPlan,
)
from app.application.planning_process import PlanningProcessPreflight
from app.application.substitution_variables import (
    SubstitutionVariableCatalog,
)
from app.application.reports import (
    ReportCatalogItem,
    ReportPreflight,
)
from app.config.settings import Settings
from app.models.data_integration_catalog import DataIntegrationDefinition
from app.models.api_token import ApiTokenScope
from app.models.access_control import RoleCode, TriggerSource
from app.models.workflow import (
    WorkflowRun,
    WorkflowStatus,
    WorkflowStepResult,
    WorkflowStepStatus,
)
from app.services.workflow_repository import SQLWorkflowRepository
from app.models.data_validation import (
    DataComparisonCell,
    DataQualityIssue,
    DataQualityResult,
    DataQualityRules,
    DataMismatch,
    DataValidationResult,
    FormGrid,
    FormGridRow,
)
from app.models.pipeline_catalog import PipelineCatalogDefinition
from app.models.environment import DimensionInfo, MemberInfo
from app.models.file_transfer import OracleRepositoryFile
from app.models.oracle_artifact import (
    OracleArtifact,
    OracleArtifactSource,
    OracleArtifactStatus,
    OracleArtifactType,
)
from app.models.automation_schedule import (
    AutomationConcurrencyPolicy,
    AutomationInputPolicy,
    AutomationMisfirePolicy,
    AutomationSchedule,
    AutomationScheduleFrequency,
    AutomationScheduleRun,
    AutomationScheduleRunEvidence,
    AutomationScheduleRunStatus,
    AutomationTargetType,
)
from app.models.substitution_variable import SubstitutionVariable
from app.utils.exceptions import AuthenticationError
from app.web import application as web_application
from app.web.application import _agent_tool_activity_payload, create_app


@pytest.mark.parametrize(
    "tool_name",
    ("list_variance_views", "review_saved_variance"),
)
def test_variance_tool_results_are_exposed_to_the_agent_workspace(
    tool_name: str,
) -> None:
    result = {"purpose": "variance", "views": [], "count": 0, "total_count": 0}

    payload = _agent_tool_activity_payload(
        AgentToolActivity(
            name=tool_name,
            arguments={},
            status="SUCCESS",
            summary="Variance review completed.",
            result=result,
        )
    )

    assert payload["result"] == result


def test_failed_flow_recovery_requires_review_and_queues_new_execution(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path), session_secret="test-secret")
    client = TestClient(app)
    _login(client)
    plan = StandaloneFlowRecoveryPlan(
        source_execution_id="failed-flow",
        flow_name="Monthly Forecast",
        failed_step_sequence=2,
        failure_reason="Import failed.",
        retryable=True,
        blocked_reason=None,
        steps=(
            RecoveryStepPreview(
                sequence=2,
                operation_code="data-import",
                display_name="Planning Data Import",
                artifact_name="Import Forecast",
                original_status="FAILED",
            ),
        ),
        required_uploads=(),
    )
    app.state.flow_recovery = Mock()
    app.state.flow_recovery.plan.return_value = plan
    app.state.flow_recovery.prepare_retry.return_value = SimpleNamespace(
        name="Recovery - Monthly Forecast",
        steps=(SimpleNamespace(),),
    )
    app.state.operation_manager = Mock()
    app.state.operation_manager.submit_flow.return_value = SimpleNamespace(
        execution_id="recovery-flow"
    )

    reviewed = client.get(
        "/api/v1/operations/runs/failed-flow/recovery"
    )
    started = client.post(
        "/api/v1/operations/runs/failed-flow/recovery",
        json={
            "failed_step_sequence": 2,
            "confirmation": "RETRY_FROM_FAILED_STEP",
            "replacement_uploads": {},
        },
    )

    assert reviewed.status_code == 200
    assert reviewed.json()["recovery"]["steps"][0]["sequence"] == 2
    assert started.status_code == 202
    assert started.json()["execution_id"] == "recovery-flow"
    app.state.flow_recovery.prepare_retry.assert_called_once_with(
        "failed-flow",
        expected_failed_step=2,
        replacement_uploads={},
    )
    actor = app.state.operation_manager.submit_flow.call_args.kwargs["actor"]
    assert actor.trigger_source is TriggerSource.MANUAL


def test_standalone_flow_stop_requests_safe_worker_boundary(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path), session_secret="test-secret")
    client = TestClient(app)
    _login(client)
    app.state.operation_manager = Mock()
    app.state.operation_manager.request_flow_stop.return_value = SimpleNamespace(
        execution_id="active-flow",
        status=OperationExecutionStatus.RUNNING,
        cancellation_requested_at=datetime(2026, 9, 17, 9, 30, tzinfo=UTC),
    )

    response = client.post("/api/v1/operations/runs/active-flow/stop")

    assert response.status_code == 200
    assert response.json()["status"] == "stop_requested"
    assert "current Oracle step will finish" in response.json()["message"]
    app.state.operation_manager.request_flow_stop.assert_called_once_with(
        "active-flow",
        requested_by="admin",
    )


class _SuccessfulConnection:
    def execute(self) -> ConnectionResult:
        return ConnectionResult(
            application_name="Vision",
            environment_url="http://epm.internal/HyperionPlanning",
        )


class _FailedConnection:
    def execute(self) -> ConnectionResult:
        raise AuthenticationError(
            "Invalid credentials.",
            status_code=401,
        )


def _settings(tmp_path: Path) -> Settings:
    process_catalog = tmp_path / "planning_processes.json"
    process_catalog.write_text(
        json.dumps(
            {
                "version": 1,
                "processes": [
                    {
                        "code": "MONTHLY_FORECAST_PROCESS",
                        "displayName": "Monthly Forecast Process",
                        "cycleCode": "MONTHLY_FORECAST",
                        "steps": [
                            {
                                "type": "PREFLIGHT",
                                "name": "Validate Process Inputs",
                            },
                            {
                                "type": "RUN_PIPELINE",
                                "name": "Run Planning Pipeline",
                            },
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    pipeline_catalog = tmp_path / "pipelines.json"
    pipeline_catalog.write_text(
        json.dumps(
            {
                "pipelines": [
                    {
                        "code": "PIPE01",
                        "name": "Forecast Pipeline",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return Settings(
        epm_base_url="http://epm.internal/HyperionPlanning",
        epm_username="administrator",
        epm_password="secret",
        application_name="Vision",
        workflow_database_file=tmp_path / "history.sqlite3",
        planning_process_catalog_file=process_catalog,
        pipeline_catalog_file=pipeline_catalog,
        report_output_dir=tmp_path / "reports",
    )


@pytest.fixture(autouse=True)
def _install_frontend_test_build(tmp_path: Path, monkeypatch) -> None:
    """Provide the browser-entry assets without relying on ignored build output."""
    frontend_root = tmp_path / "frontend-dist"
    assets_root = frontend_root / "assets"
    assets_root.mkdir(parents=True)
    (frontend_root / "index.html").write_text(
        """<!doctype html>
<html>
  <head><link rel="stylesheet" href="/assets/index.css"></head>
  <body>
    <div id="root"></div>
    <script type="module" src="/assets/index.js"></script>
  </body>
</html>
""",
        encoding="utf-8",
    )
    (assets_root / "index.css").write_text("body {}\n", encoding="utf-8")
    (assets_root / "index.js").write_text("export {};\n", encoding="utf-8")
    monkeypatch.setattr(web_application, "FRONTEND_DIST_ROOT", frontend_root)


def _csrf(response) -> str:
    payload = response.json()
    token = str(payload.get("csrf_token", "")).strip()
    assert token
    return token


def _login(client: TestClient) -> None:
    """Complete one-time bootstrap and retain a valid CSRF header."""
    setup = client.get("/api/v1/bootstrap")
    token = _csrf(setup)
    created = client.post(
        "/api/v1/access-control/bootstrap",
        headers={"X-CSRF-Token": token},
        json={
            "username": "admin",
            "display_name": "Test Administrator",
            "email": "admin@example.com",
            "password": "Test password 123!",
            "password_confirmation": "Test password 123!",
        },
    )
    assert created.status_code == 200
    workspace = client.get("/api/v1/bootstrap")
    client.headers.update({"X-CSRF-Token": _csrf(workspace)})


def test_first_run_bootstrap_does_not_expose_oracle_password(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path), session_secret="test-secret")

    response = TestClient(app).get("/api/v1/bootstrap")

    assert response.status_code == 200
    assert response.json()["requires_bootstrap"] is True
    assert "secret" not in response.text
    assert response.headers["x-frame-options"] == "DENY"


def test_environment_configuration_exposes_only_non_secret_selection(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path), session_secret="test-secret")
    client = TestClient(app)
    _login(client)

    response = client.get("/api/v1/environment/configuration")

    assert response.status_code == 200
    payload = response.json()
    assert payload["base_url"] == "http://epm.internal/HyperionPlanning"
    assert payload["active_application"] == "Vision"
    assert payload["selected_application"] == "Vision"
    assert payload["selection_source"] == "ENVIRONMENT"
    assert payload["restart_required"] is False
    assert "secret" not in response.text
    assert "administrator" not in response.text


def test_environment_application_discovery_and_selection_are_governed(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path), session_secret="test-secret")
    client = TestClient(app)
    _login(client)
    discovered = EnvironmentConfiguration(
        base_url="http://epm.internal/HyperionPlanning",
        deployment_mode="on_premises",
        selected_application="Vision",
        selection_source="ENVIRONMENT",
        applications=(
            ApplicationInfo(name="Forecast", product_type="HP"),
            ApplicationInfo(name="Vision", product_type="HP"),
        ),
        last_discovered_at=datetime.now(UTC),
        last_discovery_error=None,
        selected_at=datetime.now(UTC),
        selected_by_user_id=None,
    )
    selected = replace(
        discovered,
        selected_application="Forecast",
        selection_source="ADMIN_SELECTION",
    )
    service = Mock()
    service.discover.return_value = discovered
    service.select_application.return_value = selected
    app.state.environment_configuration = service

    refreshed = client.post("/api/v1/environment/applications/discover")
    saved = client.put(
        "/api/v1/environment/application",
        json={"application_name": "Forecast"},
    )

    assert refreshed.status_code == 200
    assert [item["name"] for item in refreshed.json()["applications"]] == [
        "Forecast",
        "Vision",
    ]
    assert saved.status_code == 200
    assert saved.json()["selected_application"] == "Forecast"
    assert saved.json()["restart_required"] is True
    assert "Restart the API and worker" in saved.json()["message"]
    service.select_application.assert_called_once()


def test_react_entry_is_available_before_authentication(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path), session_secret="test-secret")

    response = TestClient(app).get("/app", follow_redirects=False)

    assert response.status_code == 200
    assert '<div id="root"></div>' in response.text


def test_react_entry_serves_its_compiled_assets(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path), session_secret="test-secret")
    client = TestClient(app)

    entry = client.get("/app", follow_redirects=False)
    asset_paths = re.findall(r'(?:src|href)="([^"]*/assets/[^"]+)"', entry.text)

    assert asset_paths
    for asset_path in asset_paths:
        response = client.get(asset_path)
        assert response.status_code == 200


def test_legacy_agent_page_redirects_to_react_workspace(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path), session_secret="test-secret")
    client = TestClient(app)
    _login(client)

    response = client.get("/app/agent", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/#assistant"


def test_configured_react_origin_retires_duplicate_operation_page(
    tmp_path: Path,
) -> None:
    settings = replace(
        _settings(tmp_path),
        web_frontend_url="https://workspace.example.com",
    )
    app = create_app(settings, session_secret="test-secret")
    client = TestClient(app)
    setup = client.get("/api/v1/bootstrap")
    created = client.post(
        "/api/v1/access-control/bootstrap",
        headers={"X-CSRF-Token": _csrf(setup)},
        json={
            "username": "admin",
            "display_name": "Test Administrator",
            "email": "admin@example.com",
            "password": "Test password 123!",
            "password_confirmation": "Test password 123!",
        },
    )
    assert created.status_code == 200

    response = client.get(
        "/app/operations/data-integrations?planning_task_id=14",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        "https://workspace.example.com/?planning_task_id=14"
        "&operation=data-integrations#operations"
    )


def test_v1_bootstrap_supports_an_independent_unauthenticated_client(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path), session_secret="test-secret")

    response = TestClient(app).get("/api/v1/bootstrap")

    assert response.status_code == 200
    payload = response.json()
    assert payload["product"] == {
        "name": "Oracle EPM Automation Platform",
        "company": "BISP Solutions",
        "api_version": "v1",
    }
    assert payload["authenticated"] is False
    assert payload["requires_bootstrap"] is True
    assert payload["csrf_token"]
    assert payload["environment"] is None
    assert payload["user"] is None
    assert payload["navigation"] == []


def test_public_service_probes_do_not_require_a_browser_session(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path), session_secret="test-secret")
    client = TestClient(app)

    live = client.get("/health/live")
    ready = client.get("/health/ready")

    assert live.status_code == 200
    assert live.json() == {
        "status": "alive",
        "service": "bisp-epm-api",
        "version": "0.1.0",
    }
    assert ready.status_code == 200
    assert ready.json() == {
        "status": "ready",
        "service": "bisp-epm-api",
        "version": "0.1.0",
        "components": {"database": "available"},
        "environment_configured": True,
    }


def test_readiness_probe_hides_database_failure_details(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path), session_secret="test-secret")

    class UnavailableDatabase:
        def connect(self):
            raise OperationalError(
                "SELECT 1",
                {},
                Exception("password authentication failed: secret-value"),
            )

    app.state.platform_database = UnavailableDatabase()
    response = TestClient(app).get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "service": "bisp-epm-api",
        "version": "0.1.0",
        "components": {"database": "unavailable"},
    }
    assert "secret-value" not in response.text


def test_v1_bootstrap_returns_effective_user_and_navigation(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path), session_secret="test-secret")
    client = TestClient(app)
    _login(client)

    response = client.get("/api/v1/bootstrap")

    assert response.status_code == 200
    payload = response.json()
    assert payload["authenticated"] is True
    assert payload["requires_bootstrap"] is False
    assert payload["environment"] == {
        "application_name": "Vision",
        "deployment_mode": "on_premises",
        "base_url": "http://epm.internal/HyperionPlanning",
        "configured": True,
        "execution_account": "administrator",
    }
    assert payload["user"]["username"] == "admin"
    assert payload["user"]["platform_roles"] == [
        "SERVICE_ADMINISTRATOR"
    ]
    assert payload["user"]["persona"] == "SERVICE_ADMINISTRATOR"
    assert payload["user"]["persona_label"] == "Service Administrator"
    assert "user.manage" in payload["user"]["permissions"]
    assert {item["code"] for item in payload["navigation"]} >= {
        "home",
        "tasks",
        "cycles",
        "jobs",
        "operations",
        "access-control",
    }
    assert "process-designer" not in {
        item["code"] for item in payload["navigation"]
    }
    assert "reports" not in {
        item["code"] for item in payload["navigation"]
    }
    data_explorer_navigation = next(
        item
        for item in payload["navigation"]
        if item["code"] == "data-review"
    )
    assert data_explorer_navigation["label"] == "Data Explorer"
    operations_navigation = next(
        item for item in payload["navigation"] if item["code"] == "operations"
    )
    schedules_navigation = next(
        item for item in payload["navigation"] if item["code"] == "schedules"
    )
    assert operations_navigation["path"] == "#operations"
    assert schedules_navigation["path"] == "#schedules"
    assert payload["features"] == {
            "legacy_ui": False,
        "task_engine": True,
        "planning_cycles": True,
        "approvals": True,
        "notifications": True,
        "access_control": True,
        "jobs_activity": True,
    }


def test_v1_operations_returns_role_authorized_standalone_services(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path), session_secret="test-secret")
    client = TestClient(app)
    _login(client)

    response = client.get("/api/v1/operations")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert {item["code"] for item in payload["operations"]} == {
        definition.code for definition in OPERATION_DEFINITIONS
    }
    assert next(
        item
        for item in payload["operations"]
        if item["code"] == "business-rules"
    ) == {
        "code": "business-rules",
        "display_name": "Business Rules",
        "description": (
            "Run deployed Calculation Manager rules with optional runtime "
            "prompt values."
        ),
        "category": "Calculation",
        "risk_level": "Controlled",
        "route": "/app/operations/business-rules",
    }


def test_v1_session_login_and_logout_rotate_browser_security_state(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path), session_secret="test-secret")
    client = TestClient(app)
    _login(client)

    signed_out = client.delete("/api/v1/session")
    assert signed_out.status_code == 200
    assert signed_out.json()["status"] == "success"

    anonymous = client.get("/api/v1/bootstrap").json()
    assert anonymous["authenticated"] is False
    signed_in = client.post(
        "/api/v1/session",
        headers={"X-CSRF-Token": anonymous["csrf_token"]},
        json={
            "username": "admin",
            "password": "Test password 123!",
        },
    )

    assert signed_in.status_code == 200
    payload = signed_in.json()
    assert payload["status"] == "success"
    assert payload["csrf_token"]
    assert payload["csrf_token"] != anonymous["csrf_token"]
    assert payload["user"]["username"] == "admin"
    assert client.get("/api/v1/bootstrap").json()["authenticated"] is True


def test_oracle_credentials_are_advertised_and_start_a_platform_session(
    tmp_path: Path,
) -> None:
    settings = replace(
        _settings(tmp_path),
        epm_base_url="https://example.epm.oraclecloud.com",
        deployment_mode="cloud",
    )
    app = create_app(settings, session_secret="test-secret")
    client = TestClient(app)
    _login(client)
    administrator = app.state.access_control.list_users()[0]
    oracle_authentication = Mock(return_value=administrator)
    app.state.oracle_password_authentication = SimpleNamespace(
        authenticate=oracle_authentication
    )
    client.delete("/api/v1/session")
    anonymous = client.get("/api/v1/bootstrap").json()

    response = client.post(
        "/api/v1/session/oracle",
        headers={"X-CSRF-Token": anonymous["csrf_token"]},
        json={
            "username": "planner@example.com",
            "password": "temporary-oracle-password",
        },
    )

    assert anonymous["identity_authentication"]["oracle_credentials_enabled"] is True
    assert response.status_code == 200
    assert response.json()["user"]["username"] == administrator.username
    assert client.get("/api/v1/bootstrap").json()["authenticated"] is True
    oracle_authentication.assert_called_once_with(
        "planner@example.com",
        "temporary-oracle-password",
        ip_address="testclient",
    )


def test_v1_planning_cycle_drives_dependency_aware_homepage(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path), session_secret="test-secret")
    client = TestClient(app)
    _login(client)
    created = client.post(
        "/api/v1/planning-cycles",
        json={
            "code": "AUG_FORECAST_FY27",
            "name": "August Forecast FY27",
            "cycle_type": "FORECAST",
            "scenario": "Forecast",
            "year": "FY27",
            "actual_through_period": "Jul",
            "forecast_start_period": "Aug",
            "start_date": "2027-08-03",
            "due_date": "2027-08-10",
            "stages": [
                {
                    "code": "RECONCILIATION",
                    "name": "Actual Reconciliation",
                    "sequence": 1,
                },
                {
                    "code": "PLANNER_INPUT",
                    "name": "Planner Input",
                    "sequence": 2,
                },
            ],
            "tasks": [
                {
                    "key": "RECONCILE_ACTUALS",
                    "stage_code": "RECONCILIATION",
                    "title": "Reconcile July Actuals",
                    "task_type": "DATA_VALIDATION",
                    "priority": "HIGH",
                    "assigned_role_code": "SERVICE_ADMINISTRATOR",
                    "due_at": "2027-08-03T12:00:00Z",
                    "action_type": "OPEN_MY_WORK",
                },
                {
                    "key": "UPDATE_FORECAST",
                    "stage_code": "PLANNER_INPUT",
                    "title": "Update August Forecast",
                    "task_type": "PLANNER_INPUT",
                    "assigned_role_code": "SERVICE_ADMINISTRATOR",
                    "due_at": "2027-08-08T12:00:00Z",
                    "action_type": "OPEN_FORM",
                    "depends_on": ["RECONCILE_ACTUALS"],
                },
            ],
        },
    )

    assert created.status_code == 201
    home = client.get("/api/v1/home")
    assert home.status_code == 200
    tasks = home.json()["tasks"]
    assert [item["readiness"] for item in tasks] == ["READY", "WAITING"]
    assert home.json()["cycles"][0]["progress_percent"] == 0

    workspace = client.get("/api/v1/planning-tasks")
    assert workspace.status_code == 200
    assert len(workspace.json()["tasks"]) == 2
    assert workspace.json()["cycles"][0]["current_stage"]["code"] == (
        "RECONCILIATION"
    )

    administration = client.get("/api/v1/planning-cycle-administration")
    assert administration.status_code == 200
    assert administration.json()["cycles"][0]["name"] == (
        "August Forecast FY27"
    )
    assert administration.json()["users"][0]["username"] == "admin"
    assert {item["code"] for item in administration.json()["roles"]} >= {
        "USER",
        "SERVICE_ADMINISTRATOR",
    }

    blocked = client.patch(
        f"/api/v1/planning-tasks/{tasks[1]['task_id']}/status",
        json={"status": "IN_PROGRESS"},
    )
    assert blocked.status_code == 400
    first = client.patch(
        f"/api/v1/planning-tasks/{tasks[0]['task_id']}/status",
        json={"status": "COMPLETED"},
    )
    assert first.status_code == 200
    refreshed = client.get("/api/v1/home").json()
    assert refreshed["tasks"][1]["readiness"] == "READY"
    assert refreshed["cycles"][0]["progress_percent"] == 50


def test_v1_planning_submission_approval_and_notification_flow(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path), session_secret="test-secret")
    client = TestClient(app)
    _login(client)
    created = client.post(
        "/api/v1/planning-cycles",
        json={
            "code": "SEP_FORECAST_FY27",
            "name": "September Forecast FY27",
            "cycle_type": "FORECAST",
            "year": "FY27",
            "start_date": "2027-09-01",
            "due_date": "2027-09-10",
            "stages": [
                {"code": "SUBMIT", "name": "Planner Submission", "sequence": 1},
                {"code": "REVIEW", "name": "Manager Review", "sequence": 2},
            ],
            "tasks": [
                {
                    "key": "SUBMIT_FORECAST",
                    "stage_code": "SUBMIT",
                    "title": "Submit September Forecast",
                    "task_type": "SUBMISSION",
                    "assigned_role_code": "SERVICE_ADMINISTRATOR",
                    "action_type": "SUBMIT_APPROVAL",
                },
                {
                    "key": "REVIEW_FORECAST",
                    "stage_code": "REVIEW",
                    "title": "Review September Forecast",
                    "task_type": "APPROVAL",
                    "assigned_role_code": "SERVICE_ADMINISTRATOR",
                    "action_type": "REVIEW_APPROVAL",
                    "depends_on": ["SUBMIT_FORECAST"],
                },
            ],
        },
    )
    cycle_id = created.json()["cycle"]["cycle_id"]
    tasks = client.get(f"/api/v1/planning-cycles/{cycle_id}").json()["tasks"]
    submission = next(item for item in tasks if item["action_type"] == "SUBMIT_APPROVAL")

    submitted = client.post(
        f"/api/v1/planning-tasks/{submission['task_id']}/submit-for-approval"
    )
    assert submitted.status_code == 200
    approval = submitted.json()["approvals"][0]
    assert approval["status"] == "PENDING"
    assert client.get("/api/v1/planning-approvals").json()["approvals"][0][
        "approval_id"
    ] == approval["approval_id"]

    inbox = client.get("/api/v1/notifications").json()
    assert inbox["unread_count"] == 1
    notification_id = inbox["notifications"][0]["notification_id"]
    assert client.patch(
        f"/api/v1/notifications/{notification_id}/read"
    ).status_code == 200

    returned = client.patch(
        f"/api/v1/planning-approvals/{approval['approval_id']}",
        json={
            "decision": "RETURNED",
            "comment": "Update the volume assumption.",
        },
    )
    assert returned.status_code == 200
    assert returned.json()["approval"]["status"] == "RETURNED"
    reopened = client.get(f"/api/v1/planning-cycles/{cycle_id}").json()["tasks"]
    assert reopened[0]["status"] == "IN_PROGRESS"
    assert reopened[1]["readiness"] == "WAITING"


def test_agent_action_draft_preflight_api_is_non_executing(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings, session_secret="test-secret")
    client = TestClient(app)
    _login(client)
    repository = SQLiteAgentRepository(settings.workflow_database_file)
    conversation = repository.create_conversation(
        user_id=1,
        provider="gemini",
        model="test-model",
    )
    assistant = repository.add_message(
        conversation_id=conversation.conversation_id,
        user_id=1,
        role=AgentMessageRole.ASSISTANT,
        content="A substitution-variable action draft is ready.",
    )
    draft = repository.create_action_draft(
        conversation_id=conversation.conversation_id,
        user_id=1,
        message_id=assistant.message_id,
        payload={
            "action_type": "operation",
            "target_code": "substitution-variables",
            "display_name": "Substitution Variables",
            "category": "Application administration",
            "risk_level": "Elevated",
            "route": "/app/operations/substitution-variables",
            "objective": "Review the CurYr variable before updating it.",
            "artifact_name": "CurYr",
            "required_inputs": ["Variable scope", "New value"],
            "stages": [],
            "approval_required": True,
            "status": "PREPARED_NOT_EXECUTED",
        },
    )

    response = client.post(
        f"/api/agent/action-drafts/{draft.draft_id}/preflight"
    )

    assert response.status_code == 200
    result = response.json()["action_draft"]
    assert result["preflight_status"] == "NEEDS_INPUT"

    saved = client.patch(
        f"/api/agent/action-drafts/{draft.draft_id}/inputs",
        json={
            "inputs": {
                "scope": "ALL",
                "new_value": "FY27",
                "create_if_missing": False,
            }
        },
    )
    assert saved.status_code == 200
    assert saved.json()["action_draft"]["preflight_status"] is None

    response = client.post(
        f"/api/agent/action-drafts/{draft.draft_id}/preflight"
    )
    result = response.json()["action_draft"]
    assert result["preflight_status"] == "READY_FOR_GOVERNED_REVIEW"
    assert [item["status"] for item in result["preflight_checks"]] == [
        "PASS",
        "PASS",
        "PASS",
        "PASS",
    ]
    assert result["status"] == "PREPARED_NOT_EXECUTED"

    handoff = client.get(
        "/app/operations/substitution-variables"
        f"?agent_draft={draft.draft_id}",
        follow_redirects=False,
    )
    wrong_screen = client.get(
        f"/app/reports?agent_draft={draft.draft_id}",
        follow_redirects=False,
    )

    assert handoff.status_code == 303
    assert f"agent_draft={draft.draft_id}" in handoff.headers["location"]
    assert "operation=substitution-variables" in handoff.headers["location"]
    assert wrong_screen.status_code == 303
    assert f"agent_draft={draft.draft_id}" in wrong_screen.headers["location"]


def test_process_agent_handoff_migrates_and_embeds_business_context(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings, session_secret="test-secret")
    client = TestClient(app)
    _login(client)
    repository = SQLiteAgentRepository(settings.workflow_database_file)
    conversation = repository.create_conversation(
        user_id=1,
        provider="gemini",
        model="test-model",
    )
    assistant = repository.add_message(
        conversation_id=conversation.conversation_id,
        user_id=1,
        role=AgentMessageRole.ASSISTANT,
        content="Monthly Forecast preparation is ready.",
    )
    draft = repository.create_action_draft(
        conversation_id=conversation.conversation_id,
        user_id=1,
        message_id=assistant.message_id,
        payload={
            "action_type": "process",
            "target_code": "MONTHLY_FORECAST_PROCESS",
            "display_name": "Monthly Forecast End-to-End Process",
            "category": "Planning process",
            "risk_level": "Elevated",
            "route": (
                "/app/control-panel?process_code="
                "MONTHLY_FORECAST_PROCESS"
            ),
            "objective": "Run the FY23 January to February forecast.",
            "artifact_name": "MONTHLY_FORECAST_PROCESS",
            "required_inputs": ["Planning year"],
            "stages": ["Load", "Calculate"],
            "approval_required": True,
            "status": "PREPARED_NOT_EXECUTED",
            "input_values": {
                "planning_year": "FY23",
                "runtime_variables": {
                    "STARTPERIOD": "Jan",
                    "ENDPERIOD": "Feb",
                },
                "inbox_files": {
                    "DataLoad_File": "Data_Motors_Dataload.csv"
                },
            },
        },
    )
    repository.record_action_preflight(
        draft_id=draft.draft_id,
        user_id=1,
        status="READY_FOR_GOVERNED_REVIEW",
        checks=(),
    )

    response = client.get(
        "/app/control-panel?process_code=MONTHLY_FORECAST_PROCESS"
        f"&agent_draft={draft.draft_id}",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "process_code=MONTHLY_FORECAST_PROCESS" in response.headers["location"]
    assert f"agent_draft={draft.draft_id}" in response.headers["location"]


def test_access_control_creates_user_and_enforces_server_permissions(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path), session_secret="test-secret")
    client = TestClient(app)
    _login(client)

    page = client.get("/app/access-control")
    created = client.post(
        "/api/access/users",
        json={
            "username": "auditor",
            "display_name": "Planning Auditor",
            "email": "audit@example.com",
            "password": "Audit passphrase 123!",
            "roles": ["VIEWER"],
        },
    )

    assert page.status_code == 200
    assert '<div id="root"></div>' in page.text
    assert created.status_code == 201
    assert created.json()["user"]["permissions"] == ["agent.use", "report.generate"]

    client.delete("/api/v1/session")
    login = client.get("/api/v1/bootstrap")
    signed_in = client.post(
        "/api/v1/session",
        headers={"X-CSRF-Token": _csrf(login)},
        json={
            "username": "auditor",
            "password": "Audit passphrase 123!",
        },
    )
    assert signed_in.status_code == 200
    workspace = client.get("/api/v1/bootstrap")
    client.headers.update({"X-CSRF-Token": _csrf(workspace)})

    forbidden_api = client.get("/api/access/users")
    forbidden_page = client.get(
        "/app/access-control",
        follow_redirects=False,
    )
    history = client.get("/app/history")

    assert forbidden_api.status_code == 403
    assert forbidden_page.status_code == 303
    assert history.status_code == 200
    assert "Access Control" not in workspace.text
    assert "Execution History" not in workspace.text


def test_v1_access_control_uses_only_four_primary_roles(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path), session_secret="test-secret")
    client = TestClient(app)
    _login(client)

    catalog = client.get("/api/v1/access-control")
    assert catalog.status_code == 200
    assert [role["code"] for role in catalog.json()["roles"]] == [
        "SERVICE_ADMINISTRATOR",
        "POWER_USER",
        "USER",
        "VIEWER",
    ]

    created = client.post(
        "/api/v1/access-control/users",
        json={
            "username": "finance.user",
            "display_name": "Finance User",
            "email": "finance@example.com",
            "password": "Finance password 123!",
            "role_code": "USER",
        },
    )
    assert created.status_code == 201

    users = client.get("/api/v1/access-control").json()["users"]
    finance_user = next(item for item in users if item["username"] == "finance.user")
    changed = client.patch(
        f"/api/v1/access-control/users/{finance_user['user_id']}",
        json={
            "display_name": "Finance Reviewer",
            "email": "finance@example.com",
            "active": True,
            "role_code": "VIEWER",
        },
    )
    assert changed.status_code == 200
    assert changed.json()["user"]["role_code"] == "VIEWER"

    password = client.post(
        f"/api/v1/access-control/users/{finance_user['user_id']}/password",
        json={
            "password": "Replacement password 123!",
            "password_confirmation": "Replacement password 123!",
        },
    )
    assert password.status_code == 200

    current_user_id = catalog.json()["current_user_id"]
    self_deactivation = client.patch(
        f"/api/v1/access-control/users/{current_user_id}",
        json={
            "display_name": "Test Administrator",
            "email": "admin@example.com",
            "active": False,
            "role_code": "SERVICE_ADMINISTRATOR",
        },
    )
    assert self_deactivation.status_code == 400
    assert "cannot deactivate" in self_deactivation.json()["detail"]


def test_v1_jobs_activity_returns_safe_step_evidence(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings, session_secret="test-secret")
    client = TestClient(app)
    _login(client)
    now = datetime.now(UTC)
    SQLWorkflowRepository(settings.database_target).save(
        WorkflowRun(
            execution_id="job-test-001",
            workflow_name="Load July Actuals",
            status=WorkflowStatus.FAILED,
            started_at=now - timedelta(seconds=45),
            completed_at=now,
            error_message="Oracle rejected 3 records.",
            initiated_by="admin",
            initiated_by_display="Test Administrator",
            trigger_source=TriggerSource.MANUAL,
            oracle_execution_username="epm.integration",
            steps=(
                WorkflowStepResult(
                    name="Import data",
                    sequence=1,
                    status=WorkflowStepStatus.FAILED,
                    started_at=now - timedelta(seconds=45),
                    completed_at=now,
                    details={
                        "job_id": 917,
                        "token": "must-not-leak",
                        "record_statistics": {
                            "source": "ORACLE_JOB_DETAILS",
                            "records_read": 100,
                            "records_processed": 97,
                            "records_rejected": 3,
                            "details": [],
                        },
                    },
                    error_message="Three records contain invalid members.",
                ),
            ),
        )
    )

    catalog = client.get("/api/v1/jobs")
    assert catalog.status_code == 200
    assert catalog.json()["summary"]["failed"] == 1
    assert catalog.json()["jobs"][0]["name"] == "Load July Actuals"
    assert catalog.json()["jobs"][0]["executed_by"] == "epm.integration"

    detail = client.get("/api/v1/jobs/job-test-001")
    assert detail.status_code == 200
    payload = detail.json()["job"]
    assert payload["executed_by"] == "epm.integration"
    assert payload["steps"][0]["details"]["job_id"] == 917
    assert payload["steps"][0]["details"]["token"] == "[redacted]"
    assert payload["record_statistics"] == {
        "source": "ORACLE_JOB_DETAILS",
        "records_read": 100,
        "records_processed": 97,
        "records_rejected": 3,
        "details": [],
    }


def test_schedule_workspace_and_list_require_verified_session(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    client = TestClient(app)

    blocked = client.get("/app/schedules", follow_redirects=False)
    _login(client)
    app.state.automation_schedule_service = Mock()
    app.state.automation_schedule_service.list_schedules.return_value = ()
    page = client.get("/app/schedules")
    schedules = client.get("/api/v1/schedules")
    legacy_schedules = client.get("/api/schedules")

    assert blocked.status_code == 303
    assert page.status_code == 200
    assert '<div id="root"></div>' in page.text
    assert schedules.status_code == 200
    assert schedules.headers["x-api-version"] == "1"
    assert schedules.json()["schedules"] == []
    assert legacy_schedules.headers["deprecation"] == "true"
    assert legacy_schedules.headers["link"] == (
        '</api/v1/schedules>; rel="successor-version"'
    )


def test_pipeline_schedule_preview_and_creation_use_automation_coordinator(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    now = datetime.now(UTC)
    saved = AutomationSchedule(
        schedule_id=11,
        environment_key="test-environment",
        name="Daily Forecast",
        target_type=AutomationTargetType.ORACLE_PIPELINE,
        target_key="PIPE01",
        frequency=AutomationScheduleFrequency.DAILY,
        timezone="UTC",
        first_run_local=datetime(2027, 1, 1, 9, 0),
        input_policy=AutomationInputPolicy.FIXED,
        configuration={"variables": {"YEAR": "FY27"}, "inbox_files": {}},
        concurrency_policy=AutomationConcurrencyPolicy.SKIP_IF_ACTIVE,
        misfire_policy=AutomationMisfirePolicy.RUN_ONCE,
        enabled=True,
        next_run_at=datetime(2027, 1, 1, 9, 0, tzinfo=UTC),
        created_at=now,
        updated_at=now,
    )
    app.state.automation_schedule_coordinator = Mock()
    app.state.automation_schedule_coordinator.create.return_value = saved
    app.state.automation_schedule_coordinator.preview.return_value = SimpleNamespace(
        next_run_at=datetime(2027, 1, 1, 9, 0, tzinfo=UTC),
        next_run_local=datetime(2027, 1, 1, 9, 0, tzinfo=UTC),
    )
    client = TestClient(app)
    _login(client)

    schedule_payload = {
        "name": "Daily Forecast",
        "target_key": "PIPE01",
        "frequency": "DAILY",
        "timezone": "UTC",
        "first_run_local": "2027-01-01T09:00:00",
        "input_policy": "FIXED",
        "variables": {"YEAR": "FY27"},
        "inbox_files": {},
        "misfire_policy": "RUN_ONCE",
        "enabled": True,
    }
    preview = client.post("/api/v1/schedules/preview", json=schedule_payload)
    created = client.post("/api/v1/schedules", json=schedule_payload)

    assert preview.status_code == 200
    assert preview.json()["next_run_at"] == "2027-01-01T09:00:00+00:00"
    assert created.status_code == 201
    assert created.json()["schedule"]["target_key"] == "PIPE01"
    submitted = app.state.automation_schedule_coordinator.create.call_args.args[0]
    assert submitted.target_key == "PIPE01"
    assert submitted.configuration["variables"] == {"YEAR": "FY27"}
    assert submitted.first_run_local.tzinfo is None


def test_schedule_history_returns_environment_scoped_execution_evidence(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    occurred_at = datetime(2027, 1, 1, 9, 0, tzinfo=UTC)
    app.state.automation_schedule_service = Mock()
    app.state.automation_schedule_service.list_run_evidence.return_value = (
        AutomationScheduleRunEvidence(
            run=AutomationScheduleRun(
                run_id=7,
                schedule_id=11,
                scheduled_for=occurred_at,
                claimed_at=occurred_at,
                completed_at=occurred_at,
                status=AutomationScheduleRunStatus.SUBMITTED,
                execution_id="execution-7",
                resolved_payload={},
            ),
            schedule_name="Daily Forecast",
            target_type=AutomationTargetType.ORACLE_PIPELINE,
            target_key="PIPE01",
        ),
    )
    client = TestClient(app)
    _login(client)

    response = client.get(
        "/api/v1/schedules/runs/history?status=SUBMITTED&limit=25"
    )

    assert response.status_code == 200
    assert response.json()["summary"] == {
        "total": 1,
        "submitted": 1,
        "completed": 0,
        "failed": 0,
        "skipped": 0,
        "claimed": 0,
    }
    assert response.json()["runs"][0]["execution_id"] == "execution-7"
    kwargs = app.state.automation_schedule_service.list_run_evidence.call_args.kwargs
    assert kwargs["status"] is AutomationScheduleRunStatus.SUBMITTED
    assert kwargs["limit"] == 25


def test_successful_connection_opens_real_catalog_dashboard(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    client = TestClient(app)

    _login(client)
    dashboard = client.get("/app")
    snapshot = client.get("/api/control-panel/snapshot")

    assert dashboard.status_code == 200
    assert '<div id="root"></div>' in dashboard.text
    assert snapshot.json()["snapshot"]["application_name"] == "Vision"
    assert snapshot.json()["snapshot"]["processes"][0]["name"] == (
        "Monthly Forecast Process"
    )


def test_planning_control_panel_combines_process_and_operational_context(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    client = TestClient(app)
    _login(client)

    page = client.get("/app/control-panel")
    snapshot = client.get("/api/control-panel/snapshot")

    assert page.status_code == 200
    assert '<div id="root"></div>' in page.text
    assert snapshot.status_code == 200
    assert snapshot.json()["snapshot"]["application_name"] == "Vision"
    assert snapshot.json()["snapshot"]["processes"][0]["code"] == (
        "MONTHLY_FORECAST_PROCESS"
    )


def test_process_designer_saves_and_activates_pipeline_process(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    app.state.process_designer.inspect_pipeline = Mock(
        return_value=PipelineOperationPreview(
            code="PIPE01",
            display_name="Forecast Pipeline",
            variables=(
                PipelineVariablePreview(
                    name="STARTPERIOD",
                    display_name="Start Period",
                    default_value=None,
                    required=True,
                    editable=True,
                ),
                PipelineVariablePreview(
                    name="ENDPERIOD",
                    display_name="End Period",
                    default_value=None,
                    required=True,
                    editable=True,
                ),
            ),
            file_requirements=(),
            stages=(
                PipelineStagePreview(
                    name="LOAD_FORECAST",
                    display_name="Load Forecast Data",
                    job_count=2,
                    runs_in_parallel=False,
                ),
            ),
        )
    )
    client = TestClient(app)
    _login(client)

    page = client.get("/app/process-designer")
    draft = client.post(
        "/api/process-designer/drafts",
        json={
            "code": "REVENUE_FORECAST",
            "display_name": "Revenue Forecast",
            "pipeline_code": "PIPE01",
            "context_mode": "PIPELINE_DEFAULTS",
        },
    )
    draft_page = client.get(
        "/app/process-designer?process_code=REVENUE_FORECAST"
    )
    activated = client.post(
        "/api/process-designer/REVENUE_FORECAST/versions/1/activate"
    )
    profile_without_year = client.post(
        "/api/process-designer/REVENUE_FORECAST/presets",
        json={"name": "Invalid preset"},
    )
    profile = client.post(
        "/api/process-designer/REVENUE_FORECAST/presets",
        json={
            "name": "Working Forecast",
            "year": "FY26",
            "start_period": "Jan",
            "end_period": "Mar",
            "scenario": "Forecast",
            "version": "Working",
            "pipeline_variables": {},
            "inbox_files": {},
            "required_upload_keys": [],
        },
    )
    original_process_service = app.state.process_service
    app.state.process_service = Mock()
    app.state.process_service.preflight.return_value = (
        PlanningProcessPreflight(
            process_code="REVENUE_FORECAST",
            process_name="Revenue Forecast",
            cycle_code="REVENUE_FORECAST_CYCLE",
            pipeline_code="PIPE01",
            data_map_name=None,
            report_name=None,
            validation_enabled=False,
            run_refresh=False,
            run_data_map=False,
            clear_target=False,
            run_report=False,
            variable_changes=(),
            runtime_variables=(),
            file_requirements=(),
            steps=(),
        )
    )
    app.state.execution_manager = Mock()
    app.state.execution_manager.submit.return_value = SimpleNamespace(
        execution_id="profile123"
    )
    profile_run = client.post(
        "/api/process-designer/REVENUE_FORECAST/presets/1/runs",
        json={
            "uploads": {},
        },
    )
    app.state.process_service = original_process_service
    snapshot = client.get("/api/control-panel/snapshot")
    control_panel = client.get(
        "/app/control-panel?process_code=REVENUE_FORECAST"
    )
    architecture = client.get(
        "/api/process-designer/REVENUE_FORECAST/architecture-review"
    )

    assert page.status_code == 200
    assert '<div id="root"></div>' in page.text
    assert draft.status_code == 200
    assert draft.json()["definition"]["version"] == 1
    assert draft_page.status_code == 200
    assert '<div id="root"></div>' in draft_page.text
    saved_definition = draft.json()["definition"]
    saved_steps = saved_definition["process"]["steps"]
    assert [step["step_type"] for step in saved_steps] == [
        "PREFLIGHT",
        "RUN_PIPELINE",
    ]
    pipeline_step = next(
        step for step in saved_steps if step["step_type"] == "RUN_PIPELINE"
    )
    assert pipeline_step["parameters"]["pipelineStages"][0][
        "displayName"
    ] == "Load Forecast Data"
    assert activated.status_code == 200
    assert profile_without_year.status_code == 400
    assert "Planning year is required" in (
        profile_without_year.json()["details"]
    )
    assert profile.status_code == 200
    assert profile.json()["profile"]["one_click_ready"] is True
    assert profile_run.status_code == 202
    assert profile_run.json()["execution_id"] == "profile123"
    profile_input = app.state.execution_manager.submit.call_args.args[0]
    assert profile_input.variable_updates == ()
    process_codes = {
        item["code"] for item in snapshot.json()["snapshot"]["processes"]
    }
    process_snapshot = next(
        item
        for item in snapshot.json()["snapshot"]["processes"]
        if item["code"] == "REVENUE_FORECAST"
    )
    assert "REVENUE_FORECAST" in process_codes
    assert process_snapshot["context_mode"] == "PIPELINE_DEFAULTS"
    assert process_snapshot["preset_count"] == 1
    assert '<div id="root"></div>' in control_panel.text

    workspace = client.get(
        "/app/process-designer?process_code=REVENUE_FORECAST"
    )
    assert '<div id="root"></div>' in workspace.text
    assert architecture.status_code == 200
    assert architecture.json()["review"]["aligned"] is True
    assert architecture.json()["review"]["pipeline_accepts_year"] is False

    removed = client.delete(
        "/api/process-designer/REVENUE_FORECAST/presets/1"
    )
    refreshed_workspace = client.get(
        "/app/process-designer?process_code=REVENUE_FORECAST"
    )

    assert removed.status_code == 200
    assert '<div id="root"></div>' in refreshed_workspace.text


def test_process_designer_prepares_one_offline_legacy_migration_draft(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    client = TestClient(app)
    _login(client)

    before = client.get(
        "/app/process-designer?process_code=MONTHLY_FORECAST_PROCESS"
    )
    prepared = client.post(
        "/api/process-designer/MONTHLY_FORECAST_PROCESS/migration-draft"
    )
    prepared_again = client.post(
        "/api/process-designer/MONTHLY_FORECAST_PROCESS/migration-draft"
    )
    after = client.get(
        "/app/process-designer?process_code=MONTHLY_FORECAST_PROCESS"
    )

    assert before.status_code == 200
    assert '<div id="root"></div>' in before.text
    assert prepared.status_code == 200
    assert prepared_again.status_code == 200
    assert prepared.json()["definition"]["version"] == 1
    assert prepared_again.json()["definition"]["version"] == 1
    assert [
        step["step_type"]
        for step in prepared.json()["definition"]["process"]["steps"]
    ] == ["PREFLIGHT", "RUN_PIPELINE"]
    assert prepared.json()["definition"]["process"]["steps"][0][
        "parameters"
    ] == {"pipelineOnly": True}
    assert '<div id="root"></div>' in after.text


def test_process_revision_reuses_open_draft_and_can_deactivate(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    app.state.process_designer.inspect_pipeline = Mock(
        return_value=PipelineOperationPreview(
            code="PIPE01",
            display_name="Forecast Pipeline",
            variables=(),
            file_requirements=(),
            stages=(),
        )
    )
    client = TestClient(app)
    _login(client)
    client.post(
        "/api/process-designer/drafts",
        json={
            "code": "REVENUE_FORECAST",
            "display_name": "Revenue Forecast",
            "pipeline_code": "PIPE01",
        },
    )
    client.post(
        "/api/process-designer/REVENUE_FORECAST/versions/1/activate"
    )

    first_change = client.put(
        "/api/process-designer/REVENUE_FORECAST",
        json={
            "code": "REVENUE_FORECAST",
            "display_name": "Revised Revenue Forecast",
            "pipeline_code": "PIPE01",
        },
    )
    second_change = client.put(
        "/api/process-designer/REVENUE_FORECAST",
        json={
            "code": "REVENUE_FORECAST",
            "display_name": "Final Revenue Forecast",
            "pipeline_code": "PIPE01",
        },
    )
    unchanged = client.put(
        "/api/process-designer/REVENUE_FORECAST",
        json={
            "code": "REVENUE_FORECAST",
            "display_name": "Final Revenue Forecast",
            "pipeline_code": "PIPE01",
        },
    )
    definitions = client.get("/api/process-designer/definitions")
    deactivated = client.delete(
        "/api/process-designer/REVENUE_FORECAST"
    )
    snapshot = client.get("/api/control-panel/snapshot")

    assert first_change.status_code == 200
    assert first_change.json()["definition"]["version"] == 2
    assert second_change.status_code == 200
    assert second_change.json()["definition"]["version"] == 2
    assert unchanged.status_code == 400
    summary = definitions.json()["definitions"][0]
    assert summary["latest_version"] == 2
    assert deactivated.status_code == 200
    assert "REVENUE_FORECAST" not in {
        item["code"] for item in snapshot.json()["snapshot"]["processes"]
    }


def test_health_check_verifies_oracle_application(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    client = TestClient(app)
    _login(client)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["application"] == "Vision"


def test_health_check_uses_latest_selected_application(
    tmp_path: Path,
) -> None:
    checked_settings: list[Settings] = []

    def connection_factory(settings: Settings) -> _SuccessfulConnection:
        checked_settings.append(settings)
        return _SuccessfulConnection()

    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=connection_factory,
    )
    app.state.environment_configuration.resolve_startup_settings = Mock(
        return_value=replace(
            app.state.settings,
            application_name="EBPCS",
        )
    )
    client = TestClient(app)
    _login(client)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert checked_settings[-1].application_name == "EBPCS"
    assert response.json()["active_application"] == "Vision"
    assert response.json()["restart_required"] is True


def test_health_check_reports_oracle_unavailability(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    client = TestClient(app)
    _login(client)
    app.state.connection_use_case_factory = (
        lambda settings: _FailedConnection()
    )

    response = client.get("/api/health")

    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"


def test_invalid_platform_credentials_return_actionable_error(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _FailedConnection(),
    )

    client = TestClient(app)
    _login(client)
    client.delete("/api/v1/session")
    login = client.get("/api/v1/bootstrap")
    response = client.post(
        "/api/v1/session",
        headers={"X-CSRF-Token": _csrf(login)},
        json={"username": "admin", "password": "incorrect password"},
    )

    assert response.status_code == 401
    assert "username or password" in response.json()["detail"]


def test_process_upload_requires_verified_session(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path), session_secret="test-secret")

    response = TestClient(app).post(
        "/api/uploads?filename=forecast.csv",
        content=b"Account,Jan\nRevenue,100\n",
        headers={"Content-Type": "application/octet-stream"},
    )

    assert response.status_code == 401


def test_process_upload_is_stored_with_opaque_token(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    client = TestClient(app)
    _login(client)

    response = client.post(
        "/api/uploads?filename=C%3A%5Cprivate%5Cforecast.csv",
        content=b"Account,Jan\nRevenue,100\n",
        headers={"Content-Type": "application/octet-stream"},
    )

    assert response.status_code == 200
    upload = response.json()["upload"]
    assert upload["filename"] == "forecast.csv"
    assert upload["size"] == 24
    assert len(upload["token"]) == 32
    stored_files = tuple((tmp_path / "web_uploads").rglob("forecast.csv"))
    assert len(stored_files) == 1
    assert stored_files[0].read_bytes() == b"Account,Jan\nRevenue,100\n"

    client.post("/logout")

    assert not stored_files[0].exists()


def test_report_download_rejects_non_excel_artifact(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    settings.report_output_dir.mkdir()
    (settings.report_output_dir / "debug.log").write_text(
        "private",
        encoding="utf-8",
    )
    app = create_app(
        settings,
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    client = TestClient(app)
    _login(client)

    response = client.get("/app/reports/debug.log")

    assert response.status_code == 404


def test_report_catalog_preflight_and_generation_start(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    app.state.report_workspace = Mock()
    app.state.report_workspace.catalog.return_value = (
        ReportCatalogItem(
            name="Revenue Report",
            title="Revenue Forecast",
            cube="Plan1",
            default_pov=(("Year", "FY25"),),
            rows=(("Account", ("Revenue",)),),
            columns=(("Period", ("Jan", "Feb")),),
        ),
    )
    app.state.report_workspace.preflight.return_value = ReportPreflight(
        form_name="Revenue Report",
        title="Revenue Forecast",
        cube="Plan1",
        registered=True,
        page_dimensions=("Year", "Scenario"),
        row_dimensions=("Account",),
        column_dimensions=("Period",),
        current_pov=(("Year", "FY25"), ("Scenario", "Forecast")),
        allowed_page_members=(("Year", ("FY25", "FY26")),),
    )
    app.state.report_workspace.register.return_value = ReportCatalogItem(
        name="Margin Report",
        title="Margin Analysis",
        cube="Plan1",
        default_pov=(("Year", "FY26"),),
        rows=(("Account", ("Gross Profit",)),),
        columns=(("Period", ("Jan", "Feb")),),
    )
    app.state.report_workspace.delete.return_value = ReportCatalogItem(
        name="Margin Report",
        title="Margin Analysis",
        cube="Plan1",
        default_pov=(("Year", "FY26"),),
    )
    app.state.operation_manager = Mock()
    app.state.operation_manager.submit.return_value = SimpleNamespace(
        execution_id="report123"
    )
    client = TestClient(app)
    _login(client)

    catalog = client.get("/api/reports/catalog")
    saved_views = client.get("/api/v1/data-explorer/views")
    registered = client.post(
        "/api/reports/catalog",
        json={
            "name": "Margin Report",
            "title": "Margin Analysis",
            "cube": "Plan1",
            "pov": {"Year": "FY26"},
            "columns": [
                {
                    "dimension": "Period",
                    "members": ["Jan", "Feb"],
                }
            ],
            "rows": [
                {
                    "dimension": "Account",
                    "members": ["Gross Profit"],
                }
            ],
        },
    )
    saved_view = client.post(
        "/api/v1/data-explorer/views",
        json={
            "name": "Margin Report",
            "title": "Margin Analysis",
            "cube": "Plan1",
            "pov": {"Year": "FY26"},
            "columns": [{"dimension": "Period", "members": ["Jan", "Feb"]}],
            "rows": [{"dimension": "Account", "members": ["Gross Profit"]}],
        },
    )
    deleted_view = client.delete("/api/v1/data-explorer/views/Margin%20Report")
    preflight = client.post(
        "/api/reports/preflight",
        json={"form_name": "Revenue Report"},
    )
    started = client.post(
        "/api/operations/reports/runs",
        json={
            "form_name": "Revenue Report",
            "title": "FY26 Revenue Forecast",
            "page_member_overrides": {
                "Year": "FY26",
                "Scenario": "Forecast",
            },
        },
    )

    assert catalog.status_code == 200
    assert catalog.json()["reports"][0]["cube"] == "Plan1"
    assert saved_views.status_code == 200
    assert saved_views.json()["views"][0]["rows"][0] == [
        "Account",
        ["Revenue"],
    ]
    assert registered.status_code == 201
    assert registered.json()["report"]["name"] == "Margin Report"
    assert saved_view.status_code == 201
    assert saved_view.json()["view"]["name"] == "Margin Report"
    assert deleted_view.status_code == 200
    definition = app.state.report_workspace.register.call_args.args[0]
    assert definition.columns[0].dimensions == ("Period",)
    assert definition.columns[0].members == (("Jan", "Feb"),)
    assert preflight.status_code == 200
    assert preflight.json()["preflight"]["page_dimensions"] == [
        "Year",
        "Scenario",
    ]
    assert started.status_code == 202
    assert started.json()["redirect"] == (
        "/app/operations/runs/report123"
    )
    submitted = app.state.operation_manager.submit.call_args.args[0]
    assert submitted.form_name == "Revenue Report"
    assert submitted.page_member_overrides == (
        ("Year", "FY26"),
        ("Scenario", "Forecast"),
    )


def test_process_setup_page_uses_configured_definition(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    client = TestClient(app)
    _login(client)

    response = client.get(
        "/app/processes/MONTHLY_FORECAST_PROCESS"
    )

    assert response.status_code == 200
    assert '<div id="root"></div>' in response.text


def test_preflight_api_returns_structured_result(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    preflight = PlanningProcessPreflight(
        process_code="MONTHLY_FORECAST_PROCESS",
        process_name="Monthly Forecast Process",
        cycle_code="MONTHLY_FORECAST",
        pipeline_code="PIPE01",
        data_map_name=None,
        report_name=None,
        validation_enabled=False,
        run_refresh=False,
        run_data_map=False,
        clear_target=False,
        run_report=False,
        variable_changes=(),
        runtime_variables=(),
        file_requirements=(),
        steps=(),
    )
    app.state.process_service = Mock()
    app.state.process_service.preflight.return_value = preflight
    client = TestClient(app)
    _login(client)

    response = client.post(
        "/api/processes/MONTHLY_FORECAST_PROCESS/preflight",
        json={
            "year": "FY26",
            "start_period": "Jan",
            "end_period": "Mar",
            "substitution_variable_updates": [
                {
                    "scope": "ALL",
                    "name": "CurYr",
                    "expected_current_value": "FY25",
                    "new_value": "FY26",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["preflight"]["pipeline_code"] == "PIPE01"
    submitted = app.state.process_service.preflight.call_args.args[0]
    assert submitted.variable_updates[0].scope == "ALL"
    assert submitted.variable_updates[0].new_value == "FY26"


def test_start_api_returns_background_execution_location(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    preflight = PlanningProcessPreflight(
        process_code="MONTHLY_FORECAST_PROCESS",
        process_name="Monthly Forecast Process",
        cycle_code="MONTHLY_FORECAST",
        pipeline_code="PIPE01",
        data_map_name=None,
        report_name=None,
        validation_enabled=False,
        run_refresh=False,
        run_data_map=False,
        clear_target=False,
        run_report=False,
        variable_changes=(),
        runtime_variables=(),
        file_requirements=(),
        steps=(),
    )
    app.state.process_service = Mock()
    app.state.process_service.preflight.return_value = preflight
    app.state.execution_manager = Mock()
    app.state.execution_manager.submit.return_value = SimpleNamespace(
        execution_id="abc123",
        submitted_at=datetime.now(UTC),
    )
    client = TestClient(app)
    _login(client)

    response = client.post(
        "/api/processes/MONTHLY_FORECAST_PROCESS/runs",
        json={
            "year": "FY26",
            "start_period": "Jan",
            "end_period": "Mar",
            "run_data_map": False,
            "run_report": False,
        },
    )

    assert response.status_code == 202
    assert response.json()["execution_id"] == "abc123"
    assert response.json()["redirect"] == "/app/runs/abc123"


def test_operations_center_and_execution_pages_render(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    client = TestClient(app)
    _login(client)

    responses = (
        client.get("/app/operations"),
        client.get("/app/operations/business-rules"),
        client.get("/app/operations/data-maps"),
        client.get("/app/operations/pipelines"),
        client.get("/app/operations/data-integrations"),
        client.get("/app/operations/metadata-import"),
        client.get("/app/operations/data-import"),
        client.get("/app/operations/substitution-variables"),
        client.get("/app/operations/cube-refresh"),
    )

    assert all(response.status_code == 200 for response in responses)
    assert all(
        '<div id="root"></div>' in response.text
        for response in responses
    )


def test_operation_catalog_api_returns_live_artifacts(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    app.state.operation_catalog = Mock()
    app.state.operation_catalog.discover.return_value = OperationCatalog(
        operations=OPERATION_DEFINITIONS,
        business_rules=("Calculate Revenue",),
        data_maps=("Revenue to Reporting",),
        pipelines=(),
        data_integrations=(),
    )
    client = TestClient(app)
    _login(client)

    response = client.get("/api/operations/catalog")

    assert response.status_code == 200
    assert response.json()["catalog"]["business_rules"] == [
        "Calculate Revenue"
    ]
    assert response.json()["catalog"]["data_maps"] == [
        "Revenue to Reporting"
    ]


def test_operation_catalog_api_can_load_registrations_without_oracle(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    app.state.operation_catalog = Mock()
    app.state.operation_catalog.discover_registered.return_value = (
        OperationCatalog(
            operations=OPERATION_DEFINITIONS,
            business_rules=(),
            data_maps=(),
            pipelines=(
                PipelineCatalogDefinition(
                    code="PIPE01",
                    name="Forecast Pipeline",
                ),
            ),
            data_integrations=(
                DataIntegrationDefinition(name="Forecast Load"),
            ),
        )
    )
    client = TestClient(app)
    _login(client)

    response = client.get(
        "/api/operations/catalog?include_live=false"
    )

    assert response.status_code == 200
    assert response.json()["catalog"]["pipelines"][0]["code"] == "PIPE01"
    assert (
        response.json()["catalog"]["data_integrations"][0]["name"]
        == "Forecast Load"
    )
    app.state.operation_catalog.discover.assert_not_called()


def test_pipeline_catalog_api_returns_registered_pipelines_without_oracle(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    app.state.operation_catalog = Mock()
    app.state.operation_catalog.discover_registered.return_value = (
        OperationCatalog(
            operations=OPERATION_DEFINITIONS,
            business_rules=(),
            data_maps=(),
            pipelines=(
                PipelineCatalogDefinition(
                    code="PIPE01",
                    name="Forecast Pipeline",
                    description="Monthly forecast lifecycle",
                ),
            ),
            data_integrations=(),
        )
    )
    client = TestClient(app)
    _login(client)

    response = client.get("/api/operations/pipelines/catalog")

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "pipelines": [
            {
                "code": "PIPE01",
                "name": "Forecast Pipeline",
                "description": "Monthly forecast lifecycle",
            }
        ],
        "artifacts": [],
    }
    app.state.operation_catalog.discover_registered.assert_called_once_with()


def test_data_import_catalog_api_uses_targeted_live_discovery(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    app.state.operation_catalog = Mock()
    app.state.operation_catalog.discover_job_names.return_value = (
        "Import Forecast Data",
        "Import Actual Data",
    )
    client = TestClient(app)
    _login(client)

    response = client.get("/api/operations/data-import/catalog")

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "jobs": ["Import Forecast Data", "Import Actual Data"],
    }
    app.state.operation_catalog.discover_job_names.assert_called_once_with(
        job_type="IMPORT_DATA"
    )


def test_oracle_file_catalog_api_returns_live_compatible_files(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    app.state.oracle_file_catalog = Mock()
    app.state.oracle_file_catalog.discover.return_value = OracleFileCatalog(
        purpose=OracleFilePurpose.DATA_IMPORT,
        files=(
            OracleRepositoryFile(
                name="inbox/Forecast_Aug.csv",
                folder="inbox",
                file_type="EXTERNAL",
                size_bytes=2048,
                last_modified_epoch_ms=1786420800000,
            ),
        ),
    )
    client = TestClient(app)
    _login(client)

    response = client.get(
        "/api/operations/files/catalog?purpose=data-import"
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "purpose": "data-import",
        "files": [
            {
                "name": "inbox/Forecast_Aug.csv",
                "folder": "inbox",
                "file_type": "EXTERNAL",
                "size_bytes": 2048,
                "last_modified_epoch_ms": 1786420800000,
            }
        ],
    }
    app.state.oracle_file_catalog.discover.assert_called_once_with(
        OracleFilePurpose.DATA_IMPORT
    )


def test_metadata_import_catalog_returns_import_and_refresh_jobs(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    app.state.operation_catalog = Mock()
    app.state.operation_catalog.discover_job_names_for_types.return_value = {
        "IMPORT_METADATA": ("Import Products", "Import Entities"),
        "CUBE_REFRESH": ("Refresh_Cube",),
    }
    client = TestClient(app)
    _login(client)

    response = client.get("/api/operations/metadata-import/catalog")

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "jobs": ["Import Products", "Import Entities"],
        "refresh_jobs": ["Refresh_Cube"],
    }
    (
        app.state.operation_catalog.discover_job_names_for_types
        .assert_called_once_with(("IMPORT_METADATA", "CUBE_REFRESH"))
    )


def test_business_rule_start_requires_live_artifact_and_returns_monitor(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    app.state.operation_catalog = Mock()
    app.state.operation_catalog.discover_job_names.return_value = (
        "Calculate Revenue",
    )
    app.state.operation_manager = Mock()
    app.state.operation_manager.submit.return_value = SimpleNamespace(
        execution_id="rule123"
    )
    client = TestClient(app)
    _login(client)

    response = client.post(
        "/api/operations/business-rules/runs",
        json={
            "rule_name": "Calculate Revenue",
            "runtime_prompts": {"Volume": "100"},
        },
    )

    assert response.status_code == 202
    assert response.json()["redirect"] == (
        "/app/operations/runs/rule123"
    )
    submitted = app.state.operation_manager.submit.call_args.args[0]
    assert submitted.rule_name == "Calculate Revenue"
    assert submitted.runtime_prompts == {"Volume": "100"}
    app.state.operation_catalog.discover_job_names.assert_called_once_with(
        job_type="RULES"
    )


def test_business_rule_rtp_import_drives_form_and_execution_validation(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    app.state.operation_catalog = Mock()
    app.state.operation_catalog.discover_job_names.return_value = (
        "Calculate Revenue",
    )
    app.state.operation_manager = Mock()
    app.state.operation_manager.submit.return_value = SimpleNamespace(
        execution_id="registered-rule-123"
    )
    client = TestClient(app)
    _login(client)
    xml = b"""<businessRule name="Calculate Revenue" cube="Plan1">
      <rtp name="Year" label="Planning Year" type="MEMBER"
           dimension="Year" required="true" />
      <rtp name="Scenario" type="MEMBER" default="Forecast" />
    </businessRule>"""

    imported = client.post(
        "/api/operations/business-rules/rtp-registry/import?filename=rules.xml",
        content=xml,
        headers={"Content-Type": "application/octet-stream"},
    )
    definition = client.get(
        "/api/operations/business-rules/rtp-definition",
        params={"rule_name": "Calculate Revenue"},
    )
    missing = client.post(
        "/api/operations/business-rules/runs",
        json={"rule_name": "Calculate Revenue", "runtime_prompts": {}},
    )
    accepted = client.post(
        "/api/operations/business-rules/runs",
        json={
            "rule_name": "Calculate Revenue",
            "runtime_prompts": {"year": "FY27"},
        },
    )

    assert imported.status_code == 200
    assert imported.json()["result"]["prompts_imported"] == 2
    assert imported.json()["result"]["rules_added"] == 1
    assert definition.status_code == 200
    assert [
        item["name"] for item in definition.json()["definition"]["prompts"]
    ] == ["Year", "Scenario"]
    assert missing.status_code == 400
    assert "Planning Year" in missing.json()["details"]
    assert accepted.status_code == 202
    submitted = app.state.operation_manager.submit.call_args.args[0]
    assert submitted.runtime_prompts == {"Year": "FY27"}

    registry_status = client.get(
        "/api/operations/business-rules/rtp-registry/status"
    )
    assert registry_status.status_code == 200
    assert registry_status.json()["registry"]["health"] == "HEALTHY"
    assert registry_status.json()["registry"][
        "synchronized_rule_count"
    ] == 1


def test_data_map_start_uses_targeted_discovery_and_reviewed_scope(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    app.state.operation_catalog = Mock()
    app.state.operation_catalog.discover_job_names.return_value = (
        "Revenue to Reporting",
    )
    app.state.operation_manager = Mock()
    app.state.operation_manager.submit.return_value = SimpleNamespace(
        execution_id="map123"
    )
    client = TestClient(app)
    _login(client)

    response = client.post(
        "/api/operations/data-maps/runs",
        json={
            "data_map_name": "Revenue to Reporting",
            "clear_target": True,
            "member_overrides": {"Year": "FY27"},
            "exclusion_overrides": {"Entity": "No Entity"},
        },
    )

    assert response.status_code == 202
    assert response.json()["execution_id"] == "map123"
    submitted = app.state.operation_manager.submit.call_args.args[0]
    assert submitted.data_map_name == "Revenue to Reporting"
    assert submitted.clear_target is True
    assert submitted.member_overrides == {"Year": "FY27"}
    assert submitted.exclusion_overrides == {"Entity": "No Entity"}
    app.state.operation_catalog.discover_job_names.assert_called_once_with(
        job_type="PLAN_TYPE_MAP"
    )


def test_assigned_user_can_run_governed_task_but_not_standalone_operation(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path), session_secret="test-secret")
    client = TestClient(app)
    _login(client)
    administrator = app.state.access_control.list_users()[0]
    app.state.access_control.create_user(
        username="planner",
        display_name="Planning User",
        email="planner@example.com",
        password="Planner password 123!",
        roles=(RoleCode.USER,),
        actor_user_id=administrator.user_id,
    )
    created = client.post(
        "/api/v1/planning-cycles",
        json={
            "code": "GOVERNED_RULE_FY27",
            "name": "Governed Rule FY27",
            "cycle_type": "FORECAST",
            "year": "FY27",
            "start_date": "2027-08-03",
            "due_date": "2027-08-10",
            "stages": [
                {"code": "CALCULATE", "name": "Calculate", "sequence": 1}
            ],
            "tasks": [
                {
                    "key": "CALCULATE_FORECAST",
                    "stage_code": "CALCULATE",
                    "title": "Calculate forecast",
                    "task_type": "CALCULATION",
                    "assigned_role_code": "USER",
                    "action_type": "RUN_BUSINESS_RULE",
                }
            ],
        },
    )
    assert created.status_code == 201
    cycle_id = created.json()["cycle"]["cycle_id"]
    task_id = client.get(f"/api/v1/planning-cycles/{cycle_id}").json()[
        "tasks"
    ][0]["task_id"]

    client.delete("/api/v1/session")
    anonymous = client.get("/api/v1/bootstrap").json()
    signed_in = client.post(
        "/api/v1/session",
        headers={"X-CSRF-Token": anonymous["csrf_token"]},
        json={"username": "planner", "password": "Planner password 123!"},
    )
    client.headers.update({"X-CSRF-Token": signed_in.json()["csrf_token"]})

    app.state.operation_catalog = Mock()
    app.state.operation_catalog.discover_job_names.return_value = (
        "Calculate Revenue",
    )
    app.state.operation_manager = Mock()

    def submit(_, **kwargs):
        kwargs["on_queued"]("assigned-rule-123")
        return SimpleNamespace(execution_id="assigned-rule-123")

    app.state.operation_manager.submit.side_effect = submit
    standalone = client.post(
        "/api/operations/business-rules/runs",
        json={"rule_name": "Calculate Revenue", "runtime_prompts": {}},
    )
    governed = client.post(
        "/api/operations/business-rules/runs",
        json={
            "rule_name": "Calculate Revenue",
            "runtime_prompts": {},
            "planning_task_id": task_id,
        },
    )

    assert standalone.status_code == 400
    assert governed.status_code == 202
    assert governed.json()["redirect"].endswith(
        f"?planning_task_id={task_id}"
    )
    linked = client.get("/api/v1/planning-tasks").json()["tasks"][0]
    assert linked["status"] == "IN_PROGRESS"
    assert linked["execution_attempts"][0]["execution_id"] == (
        "assigned-rule-123"
    )


def test_pipeline_preflight_and_start_return_monitor_location(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    app.state.operation_catalog = Mock()
    app.state.operation_catalog.preflight_pipeline.return_value = (
        PipelineOperationPreview(
            code="PIPE01",
            display_name="Forecast Pipeline",
            variables=(),
            file_requirements=(),
            stages=(),
        )
    )
    app.state.operation_manager = Mock()
    app.state.operation_manager.submit.return_value = SimpleNamespace(
        execution_id="pipe123"
    )
    client = TestClient(app)
    _login(client)

    preflight = client.get(
        "/api/operations/pipelines/PIPE01/preflight"
    )
    started = client.post(
        "/api/operations/pipelines/runs",
        json={
            "pipeline_code": "PIPE01",
            "variables": {"STARTPERIOD": "Jan-26"},
            "uploads": {},
            "inbox_files": {},
        },
    )

    assert preflight.status_code == 200
    assert preflight.json()["preview"]["code"] == "PIPE01"
    assert started.status_code == 202
    assert started.json()["redirect"] == (
        "/app/operations/runs/pipe123"
    )


def test_excel_pipeline_api_uses_scoped_token_and_excel_audit_actor(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    app.state.operation_catalog = Mock()
    app.state.operation_catalog.preflight_pipeline.return_value = (
        PipelineOperationPreview(
            code="PIPE01",
            display_name="Forecast Pipeline",
            variables=(
                PipelineVariablePreview(
                    name="YEAR",
                    display_name="Planning Year",
                    default_value=None,
                    required=True,
                    editable=True,
                ),
            ),
            file_requirements=(),
            stages=(),
        )
    )
    app.state.operation_manager = Mock()
    app.state.operation_manager.submit.return_value = SimpleNamespace(
        execution_id="excel123"
    )
    app.state.operation_manager.get.return_value = SimpleNamespace(
        execution_id="excel123",
        operation_kind=OperationKind.PIPELINE,
        target_name="PIPE01",
        status=OperationExecutionStatus.QUEUED,
        submitted_at=datetime.now(UTC),
        error_message=None,
        log_file=tmp_path / "missing.log",
    )
    app.state.operation_manager.get_workflow.return_value = None
    client = TestClient(app)
    _login(client)
    user = app.state.access_control.list_users()[0]
    issued = app.state.api_tokens.create(
        user_id=user.user_id,
        name="Excel Pipeline Runner",
        scopes=frozenset(ApiTokenScope),
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    authorization = {"Authorization": f"Bearer {issued.token}"}

    unauthorized = TestClient(app).get(
        "/api/v1/excel/pipelines/PIPE01/preflight"
    )
    preflight = client.get(
        "/api/v1/excel/pipelines/PIPE01/preflight",
        headers=authorization,
    )
    started = client.post(
        "/api/v1/excel/pipelines/PIPE01/runs",
        headers=authorization,
        json={
            "variables": {"YEAR": "FY27"},
            "inbox_files": {},
            "confirmed": True,
        },
    )
    status = client.get(
        "/api/v1/excel/executions/excel123",
        headers=authorization,
    )

    assert unauthorized.status_code == 401
    assert preflight.status_code == 200
    assert preflight.json()["pipeline"]["code"] == "PIPE01"
    assert started.status_code == 202
    assert started.json()["status_url"] == (
        "/api/v1/excel/executions/excel123"
    )
    assert status.status_code == 200
    assert status.json()["status"] == "QUEUED"
    actor = app.state.operation_manager.submit.call_args.kwargs["actor"]
    assert actor.trigger_source is TriggerSource.EXCEL


def test_exact_pipeline_code_can_be_verified_and_registered(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    app.state.operation_catalog = Mock()
    app.state.operation_catalog.register_pipeline.return_value = (
        PipelineOperationPreview(
            code="PIPE99",
            display_name="Close Pipeline",
            variables=(),
            file_requirements=(),
            stages=(),
        )
    )
    client = TestClient(app)
    _login(client)

    response = client.post(
        "/api/operations/pipelines/register",
        json={"pipeline_code": "PIPE99"},
    )

    assert response.status_code == 200
    assert response.json()["pipeline"] == {
        "code": "PIPE99",
        "name": "Close Pipeline",
        "description": "Verified from Oracle EPM",
    }
    app.state.operation_catalog.register_pipeline.assert_called_once_with(
        "PIPE99"
    )


def test_exact_data_integration_name_can_be_registered_pending_verification(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    now = datetime.now(UTC)
    app.state.operation_catalog = Mock()
    app.state.operation_catalog.register_data_integration.return_value = (
        OracleArtifact(
            artifact_id=7,
            environment_key="environment",
            artifact_type=OracleArtifactType.DATA_INTEGRATION,
            oracle_identifier="Revenue_Load",
            display_name="Revenue_Load",
            description="Awaiting first governed verification run",
            source=OracleArtifactSource.MANUAL,
            status=OracleArtifactStatus.PENDING,
            is_active=True,
            consecutive_missing_count=0,
            last_verified_at=None,
            last_error=None,
            created_at=now,
            updated_at=now,
        )
    )
    client = TestClient(app)
    _login(client)

    response = client.post(
        "/api/operations/data-integrations/register",
        json={"integration_name": "Revenue_Load"},
    )

    assert response.status_code == 200
    assert response.json()["integration"]["status"] == "PENDING"
    app.state.operation_catalog.register_data_integration.assert_called_once_with(
        "Revenue_Load",
        description=None,
    )


def test_versioned_oracle_catalog_returns_environment_scoped_sync_health(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    now = datetime.now(UTC)
    artifact = OracleArtifact(
        artifact_id=8,
        environment_key="environment",
        artifact_type=OracleArtifactType.BUSINESS_RULE,
        oracle_identifier="Calculate Revenue",
        display_name="Calculate Revenue",
        description="Discovered from Oracle RULES",
        source=OracleArtifactSource.LIVE_DISCOVERY,
        status=OracleArtifactStatus.VERIFIED,
        is_active=True,
        consecutive_missing_count=0,
        last_verified_at=now,
        last_error=None,
        created_at=now,
        updated_at=now,
    )
    app.state.operation_catalog = Mock()
    app.state.operation_catalog.synchronized_catalog.return_value = (artifact,)
    client = TestClient(app)
    _login(client)

    response = client.get("/api/v1/operations/oracle-catalog")

    assert response.status_code == 200
    assert response.headers["X-API-Version"] == "1"
    assert response.json()["summary"]["verified"] == 1
    assert response.json()["artifacts"][0]["artifact_type"] == "BUSINESS_RULE"


def test_catalog_sync_returns_complete_live_artifact_snapshot(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    now = datetime.now(UTC)
    app.state.operation_catalog = Mock()
    app.state.operation_catalog.synchronize_artifacts.return_value = (
        OracleArtifactSyncResult(
            environment_key="environment",
            application_name="Vision",
            oracle_available=True,
            verified_pipelines=1,
            missing_pipelines=0,
            discovered_integrations=1,
            verified_integrations=1,
            missing_integrations=0,
            pending_integrations=0,
            verification_errors=0,
            verified_business_rules=2,
            verified_cubes=3,
            total_verified=7,
            total_hidden=0,
            synchronized_at=now,
            message="Catalog synchronized.",
        )
    )
    app.state.operation_catalog.synchronized_catalog.return_value = ()
    client = TestClient(app)
    _login(client)

    response = client.post("/api/v1/operations/oracle-catalog/sync")

    assert response.status_code == 200
    assert response.json()["sync"]["verified_business_rules"] == 2
    assert response.json()["sync"]["verified_cubes"] == 3
    assert response.json()["sync"]["total_verified"] == 7


def test_data_integration_start_preserves_existing_inbox_reference(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    app.state.operation_catalog = Mock()
    app.state.operation_catalog.discover_registered.return_value = OperationCatalog(
        operations=OPERATION_DEFINITIONS,
        business_rules=(),
        data_maps=(),
        pipelines=(
            PipelineCatalogDefinition(
                code="PIPE01",
                name="Forecast Pipeline",
            ),
        ),
        data_integrations=(
            DataIntegrationDefinition(name="Forecast Load"),
        ),
    )
    app.state.operation_manager = Mock()
    app.state.operation_manager.submit.return_value = SimpleNamespace(
        execution_id="di123"
    )
    client = TestClient(app)
    _login(client)

    response = client.post(
        "/api/operations/data-integrations/runs",
        json={
            "integration_name": "Forecast Load",
            "start_period": "Jan-26",
            "end_period": "Mar-26",
            "import_mode": "Replace",
            "export_mode": "Merge",
            "inbox_file": "#epminbox/forecast.csv",
        },
    )

    assert response.status_code == 202
    assert response.json()["redirect"] == (
        "/app/operations/runs/di123"
    )
    submitted = app.state.operation_manager.submit.call_args.args[0]
    assert submitted.inbox_file == "#epminbox/forecast.csv"

    configured = client.post(
        "/api/operations/data-integrations/runs",
        json={
            "integration_name": "Forecast Load",
            "start_period": "Jan-26",
            "end_period": "Mar-26",
            "import_mode": "Replace",
            "export_mode": "Merge",
            "use_configured_file": True,
        },
    )

    assert configured.status_code == 202
    configured_input = app.state.operation_manager.submit.call_args.args[0]
    assert configured_input.use_configured_file is True
    assert configured_input.inbox_file is None


def test_metadata_import_start_uses_live_job_and_optional_refresh(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    app.state.operation_catalog = Mock()
    app.state.operation_catalog.discover_job_names.side_effect = (
        lambda *, job_type: (
            ("Import Products",)
            if job_type == "IMPORT_METADATA"
            else ("RefreshCube",)
        )
    )
    app.state.operation_manager = Mock()
    app.state.operation_manager.submit.return_value = SimpleNamespace(
        execution_id="metadata123"
    )
    client = TestClient(app)
    _login(client)

    response = client.post(
        "/api/operations/metadata-import/runs",
        json={
            "job_name": "Import Products",
            "inbox_file": "Products.csv",
            "error_file_name": "Products_Errors.csv",
            "refresh_job_name": "RefreshCube",
        },
    )

    assert response.status_code == 202
    assert response.json()["redirect"] == (
        "/app/operations/runs/metadata123"
    )
    submitted = app.state.operation_manager.submit.call_args.args[0]
    assert submitted.job_name == "Import Products"
    assert submitted.inbox_file == "Products.csv"
    assert submitted.refresh_job_name == "RefreshCube"
    assert app.state.operation_catalog.discover_job_names.call_args_list[:2] == [
        call(job_type="IMPORT_METADATA"),
        call(job_type="CUBE_REFRESH"),
    ]

    configured = client.post(
        "/api/operations/metadata-import/runs",
        json={
            "job_name": "Import Products",
            "use_configured_file": True,
        },
    )

    assert configured.status_code == 202
    configured_input = app.state.operation_manager.submit.call_args.args[0]
    assert configured_input.use_configured_file is True
    assert configured_input.inbox_file is None


def test_data_import_start_uses_live_saved_job_and_inbox_file(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    app.state.operation_catalog = Mock()
    app.state.operation_catalog.discover_job_names.return_value = (
        "Import Forecast Data",
    )
    app.state.operation_manager = Mock()
    app.state.operation_manager.submit.return_value = SimpleNamespace(
        execution_id="dataimport123"
    )
    client = TestClient(app)
    _login(client)

    response = client.post(
        "/api/operations/data-import/runs",
        json={
            "job_name": "Import Forecast Data",
            "inbox_file": "Forecast_Data.csv",
            "error_file_name": "Forecast_Errors.log",
        },
    )

    assert response.status_code == 202
    assert response.json()["redirect"] == (
        "/app/operations/runs/dataimport123"
    )
    submitted = app.state.operation_manager.submit.call_args.args[0]
    assert submitted.job_name == "Import Forecast Data"
    assert submitted.inbox_file == "Forecast_Data.csv"
    assert submitted.error_file_name == "Forecast_Errors.log"
    app.state.operation_catalog.discover_job_names.assert_called_once_with(
        job_type="IMPORT_DATA"
    )

    configured = client.post(
        "/api/operations/data-import/runs",
        json={
            "job_name": "Import Forecast Data",
            "use_configured_file": True,
        },
    )

    assert configured.status_code == 202
    configured_input = app.state.operation_manager.submit.call_args.args[0]
    assert configured_input.use_configured_file is True
    assert configured_input.inbox_file is None


def test_substitution_variable_catalog_and_update_start(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    app.state.substitution_variables = Mock()
    app.state.substitution_variables.discover.return_value = (
        SubstitutionVariableCatalog(
            variables=(
                SubstitutionVariable("CurYr", "FY25", "ALL"),
            ),
            plan_types=(),
            scopes=("ALL", "Plan1"),
        )
    )
    app.state.operation_manager = Mock()
    app.state.planning_work = Mock()

    def submit_variable(_, **kwargs):
        if kwargs.get("on_queued"):
            kwargs["on_queued"]("variable123")
        return SimpleNamespace(execution_id="variable123")

    app.state.operation_manager.submit.side_effect = submit_variable
    client = TestClient(app)
    _login(client)

    catalog = client.get("/api/substitution-variables/catalog")
    started = client.post(
        "/api/operations/substitution-variables/runs",
        json={
            "action": "UPDATE",
            "scope": "ALL",
            "name": "CurYr",
            "value": "FY26",
            "expected_current_value": "FY25",
        },
    )

    assert catalog.status_code == 200
    assert catalog.json()["catalog"]["variables"][0] == {
        "name": "CurYr",
        "value": "FY25",
        "scope": "ALL",
    }
    assert started.status_code == 202
    assert started.json()["redirect"] == (
        "/app/operations/runs/variable123"
    )
    submitted = app.state.operation_manager.submit.call_args.args[0]
    assert submitted.action.value == "UPDATE"
    assert submitted.expected_current_value == "FY25"

    assigned = client.post(
        "/api/operations/substitution-variables/runs",
        json={
            "action": "UPDATE",
            "scope": "ALL",
            "name": "CurYr",
            "value": "FY27",
            "expected_current_value": "FY26",
            "planning_task_id": 42,
        },
    )

    assert assigned.status_code == 202
    assert assigned.json()["redirect"].endswith("?planning_task_id=42")
    app.state.planning_work.authorize_task_execution.assert_called_once_with(
        42,
        action_type="UPDATE_SUBSTITUTION_VARIABLE",
        actor=ANY,
    )
    app.state.planning_work.link_task_execution.assert_called_once_with(
        42,
        "variable123",
        action_type="UPDATE_SUBSTITUTION_VARIABLE",
        actor=ANY,
    )


def test_cube_refresh_catalog_and_start_use_targeted_discovery(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    app.state.operation_catalog = Mock()
    app.state.operation_catalog.discover_job_names.return_value = (
        "Refresh_Cube",
    )
    app.state.operation_manager = Mock()
    app.state.operation_manager.submit.return_value = SimpleNamespace(
        execution_id="refresh123"
    )
    client = TestClient(app)
    _login(client)

    catalog = client.get("/api/operations/cube-refresh/catalog")
    started = client.post(
        "/api/operations/cube-refresh/runs",
        json={"job_name": "Refresh_Cube"},
    )

    assert catalog.status_code == 200
    assert catalog.json()["jobs"] == ["Refresh_Cube"]
    assert started.status_code == 202
    assert started.json()["redirect"] == (
        "/app/operations/runs/refresh123"
    )
    app.state.operation_catalog.discover_job_names.assert_called_once_with(
        job_type="CUBE_REFRESH"
    )
    submitted = app.state.operation_manager.submit.call_args.args[0]
    assert submitted.job_name == "Refresh_Cube"

    manual = client.post(
        "/api/operations/cube-refresh/runs",
        json={"job_name": "Refresh_Cube"},
    )

    assert manual.status_code == 202
    manually_submitted = (
        app.state.operation_manager.submit.call_args.args[0]
    )
    assert manually_submitted.job_name == "Refresh_Cube"


def test_business_rule_catalog_uses_targeted_live_discovery(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    app.state.operation_catalog = Mock()
    app.state.operation_catalog.discover_job_names.return_value = (
        "Calculate Revenue",
        "Aggregate Plan1",
    )
    client = TestClient(app)
    _login(client)

    response = client.get("/api/operations/business-rules/catalog")

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert response.json()["jobs"] == [
        "Calculate Revenue",
        "Aggregate Plan1",
    ]
    assert response.json()["rtp_registry"]["health"] == "EMPTY"
    assert response.json()["rtp_registry"]["live_rule_count"] == 2
    app.state.operation_catalog.discover_job_names.assert_called_once_with(
        job_type="RULES"
    )


def test_data_integration_catalog_uses_registered_environment_definitions(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    app.state.operation_catalog = Mock()
    app.state.operation_catalog.discover_registered.return_value = (
        OperationCatalog(
            operations=OPERATION_DEFINITIONS,
            business_rules=(),
            data_maps=(),
            pipelines=(),
            data_integrations=(
                DataIntegrationDefinition(
                    name="Forecast Load",
                    description="Monthly sales forecast",
                ),
            ),
        )
    )
    client = TestClient(app)
    _login(client)

    response = client.get("/api/operations/data-integrations/catalog")

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "integrations": [
            {
                "name": "Forecast Load",
                "description": "Monthly sales forecast",
            }
        ],
        "artifacts": [],
    }
    app.state.operation_catalog.discover_registered.assert_called_once_with()


def test_data_review_page_and_read_only_apis(
    tmp_path: Path,
) -> None:
    app = create_app(
        _settings(tmp_path),
        session_secret="test-secret",
        connection_use_case_factory=lambda settings: _SuccessfulConnection(),
    )
    data_review = Mock()
    data_review.list_cubes.return_value = (
        DataReviewCube("Plan1", "Plan1", 0, 7),
    )
    data_review.list_dimensions.return_value = (
        DimensionInfo("Account", "Account"),
        DimensionInfo("Period", "Period"),
    )
    data_review.search_members.return_value = DataReviewMemberSearch(
        cube="Plan1",
        dimension="Account",
        query="rev",
        members=(
            MemberInfo(
                "Revenue",
                alias="Total Revenue",
                path="/Account/Revenue",
            ),
        ),
        total_matches=1,
        has_more=False,
    )
    grid = FormGrid(
        row_dimensions=("Account",),
        column_dimensions=("Period",),
        columns=(("Jan",),),
        rows=(FormGridRow(("Revenue",), (100,)),),
        pov=(("Scenario", "Forecast"),),
    )
    data_review.load_slice.return_value = DataReviewGrid(
        cube="Plan1",
        form_name="Revenue Review",
        grid=grid,
        row_count=1,
        column_count=1,
        cell_count=1,
        missing_cell_count=0,
    )
    data_review.validate_slice.return_value = DataReviewQualityValidation(
        cube="Plan1",
        form_name="Plan1 data slice",
        result=DataQualityResult(
            status="FAIL",
            checked_cells=1,
            passed_cells=0,
            issue_count=1,
            missing_count=1,
            zero_count=0,
            below_minimum_count=0,
            above_maximum_count=0,
            non_numeric_count=0,
            issues=(
                DataQualityIssue(
                    code="MISSING",
                    severity="ERROR",
                    message="No data.",
                    pov=(("Scenario", "Forecast"),),
                    row_headers=("Revenue",),
                    column_headers=("Jan",),
                    raw_value="#Missing",
                    numeric_value=None,
                ),
            ),
            truncated=False,
            rules=DataQualityRules(),
        ),
    )
    data_review.compare_slices.return_value = DataReviewComparison(
        source_cube="Plan1",
        target_cube="Rpt",
        result=DataValidationResult(
            source_form="Source Review",
            target_form="Target Review",
            compared_cells=1,
            matched_cells=0,
            mismatches=(
                DataMismatch(
                    row_headers=("Revenue",),
                    column_headers=("Jan",),
                    source_value=Decimal("100"),
                    target_value=Decimal("99"),
                    difference=Decimal("1"),
                ),
            ),
            tolerance=Decimal("0"),
            cells=(
                DataComparisonCell(
                    row_headers=("Revenue",),
                    column_headers=("Jan",),
                    source_value=Decimal("100"),
                    target_value=Decimal("99"),
                    difference=Decimal("1"),
                    matches=False,
                ),
            ),
        ),
    )
    app.state.data_review = data_review
    client = TestClient(app)
    _login(client)

    page = client.get("/app/data-review")
    cubes = client.get("/api/data-review/cubes")
    dimensions = client.get(
        "/api/data-review/cubes/Plan1/dimensions"
    )
    members = client.get(
        "/api/data-review/cubes/Plan1/dimensions/Account/members",
        params={"q": "rev", "offset": 10, "limit": 20},
    )
    reviewed = client.post(
        "/api/data-review/grid",
        json={
            "cube": "Plan1",
            "pov": {"Scenario": "Forecast"},
            "columns": [{"dimension": "Period", "members": ["Jan"]}],
            "rows": [{"dimension": "Account", "members": ["Revenue"]}],
        },
    )
    grid_export = client.post(
        "/api/data-review/grid/export",
        json={
            "cube": "Plan1",
            "pov": {"Scenario": "Forecast"},
            "columns": [{"dimension": "Period", "members": ["Jan"]}],
            "rows": [{"dimension": "Account", "members": ["Revenue"]}],
        },
    )
    csv_export = client.post(
        "/api/v1/data-review/grid/export/csv",
        json={
            "cube": "Plan1",
            "pov": {"Scenario": "Forecast"},
            "columns": [{"dimension": "Period", "members": ["Jan"]}],
            "rows": [{"dimension": "Account", "members": ["Revenue"]}],
        },
    )
    validation_payload = {
        "slice": {
            "cube": "Plan1",
            "pov": {"Scenario": "Forecast"},
            "columns": [{"dimension": "Period", "members": ["Jan"]}],
            "rows": [{"dimension": "Account", "members": ["Revenue"]}],
        },
        "rules": {
            "check_missing": True,
            "check_zero": False,
            "minimum": None,
            "maximum": None,
            "max_issues": 500,
        },
    }
    validated = client.post(
        "/api/data-review/validate",
        json=validation_payload,
    )
    validation_export = client.post(
        "/api/data-review/validate/export",
        json=validation_payload,
    )
    compared = client.post(
        "/api/data-review/compare",
        json={
            "source": {
                "cube": "Plan1",
                "pov": {"Scenario": "Forecast"},
                "columns": [
                    {"dimension": "Period", "members": ["Jan"]}
                ],
                "rows": [
                    {"dimension": "Account", "members": ["Revenue"]}
                ],
            },
            "target": {
                "cube": "Rpt",
                "pov": {"Scenario": "Forecast"},
                "columns": [
                    {"dimension": "Period", "members": ["Jan"]}
                ],
                "rows": [
                    {"dimension": "Account", "members": ["Revenue"]}
                ],
            },
            "tolerance": "0",
        },
    )
    comparison_export = client.post(
        "/api/data-review/compare/export",
        json={
            "source": validation_payload["slice"],
            "target": {
                **validation_payload["slice"],
                "cube": "Rpt",
            },
            "tolerance": "0",
            "max_mismatches": 100,
        },
    )

    assert page.status_code == 200
    assert '<div id="root"></div>' in page.text
    assert cubes.json()["cubes"][0]["name"] == "Plan1"
    assert dimensions.json()["dimensions"] == [
        {"name": "Account", "dimension_type": "Account"},
        {"name": "Period", "dimension_type": "Period"},
    ]
    data_review.list_dimensions.assert_called_once_with("Plan1")
    assert members.json()["members"][0]["name"] == "Revenue"
    data_review.search_members.assert_called_once_with(
        "Plan1",
        "Account",
        query="rev",
        offset=10,
        limit=20,
    )
    assert reviewed.json()["review"]["cell_count"] == 1
    assert grid_export.status_code == 200
    assert grid_export.content.startswith(b"PK")
    assert csv_export.status_code == 200
    assert csv_export.content.decode("utf-8-sig").splitlines() == [
        "Scenario,Account,Period,Value",
        "Forecast,Revenue,Jan,100",
    ]
    assert csv_export.headers["cache-control"] == "no-store"
    assert validated.json()["validation"]["result"]["status"] == "FAIL"
    assert validation_export.status_code == 200
    assert validation_export.content.startswith(b"PK")
    assert comparison_export.status_code == 200
    assert comparison_export.content.startswith(b"PK")
    assert compared.json()["comparison"]["result"]["mismatches"][0][
        "difference"
    ] == 1
    assert compared.json()["comparison"]["result"]["cells"][0][
        "matches"
    ] is False

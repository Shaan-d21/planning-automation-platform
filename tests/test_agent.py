"""Tests for provider-neutral, read-only agent orchestration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent.capabilities import AgentCapabilityGateway
from app.application.data_review import (
    DataReviewComparison,
    DataReviewCube,
    DataReviewGrid,
    DataReviewMemberSearch,
)
from app.agent.gemini_provider import GeminiAgentProvider
from app.agent.models import (
    AgentActionDraft,
    AgentApprovalRequest,
    AgentClarificationRequest,
    AgentInputRequest,
    AgentMessage,
    AgentMessageRole,
    AgentProviderResult,
    AgentToolActivity,
    AgentToolCall,
    AgentToolDefinition,
)
from app.agent.preflight import AgentActionPreflightService
from app.agent.repository import SQLiteAgentRepository
from app.agent.service import AgentApplicationService
from app.application.operations import (
    PipelineFilePreview,
    PipelineOperationPreview,
    PipelineStagePreview,
    PipelineVariablePreview,
)
from app.application.reports import ReportCatalogItem
from app.application.substitution_variables import SubstitutionVariableCatalog
from app.config.settings import Settings
from app.models.access_control import (
    Permission,
    RoleCode,
    TriggerSource,
    UserAccount,
)
from app.models.data_validation import (
    DataMismatch,
    DataValidationResult,
    FormGrid,
    FormGridRow,
)
from app.models.environment import DimensionInfo, MemberInfo
from app.models.substitution_variable import SubstitutionVariable
from app.models.workflow import (
    WorkflowRun,
    WorkflowStatus,
    WorkflowStepResult,
    WorkflowStepStatus,
)
from app.services.access_control_service import AccessControlService
from app.utils.exceptions import AgentCapabilityError, AgentConversationError


def _settings(tmp_path: Path, *, api_key: str | None = "test-key") -> Settings:
    return Settings(
        epm_base_url="https://example.oraclecloud.com",
        epm_username="service-user",
        epm_password="secret",
        application_name="Vision",
        workflow_database_file=tmp_path / "workflow.sqlite3",
        gemini_api_key=api_key,
    )


def _user(*permissions: Permission) -> UserAccount:
    now = datetime.now(UTC)
    return UserAccount(
        user_id=1,
        username="planner",
        display_name="Planning User",
        email=None,
        active=True,
        roles=(RoleCode.POWER_USER,),
        permissions=frozenset({Permission.AGENT_USE, *permissions}),
        created_at=now,
        updated_at=now,
    )


class _FakeProvider:
    provider_name = "fake"
    model_name = "fake-model"

    def respond(self, *, messages, system_instruction, tools, execute_tool):
        assert messages[-1].content == "What can I run?"
        result = execute_tool(
            type("Call", (), {"name": "list_platform_operations", "arguments": {}})()
        )
        assert result["count"] > 0
        return AgentProviderResult(
            text="The configured operations are available from Operations & Audit.",
            tool_activity=(
                AgentToolActivity(
                    name="list_platform_operations",
                    arguments={},
                    status="SUCCESS",
                    summary="Operations returned.",
                ),
            ),
        )


class _DraftProvider:
    provider_name = "fake"
    model_name = "fake-model"

    def respond(self, *, messages, system_instruction, tools, execute_tool):
        result = execute_tool(
            AgentToolCall(
                name="prepare_operation_action",
                arguments={
                    "operation_code": "business-rules",
                    "objective": "Calculate the approved revenue forecast.",
                    "artifact_name": "Revenue Forecast",
                },
            )
        )
        return AgentProviderResult(
            text="I prepared a governed Business Rule draft for review.",
            tool_activity=(
                AgentToolActivity(
                    name="prepare_operation_action",
                    arguments={"operation_code": "business-rules"},
                    status="SUCCESS",
                    summary="Action draft prepared.",
                    result=result,
                ),
            ),
        )


class _NeverCalledProvider:
    provider_name = "never"
    model_name = "never"

    def generate(self, **_):
        raise AssertionError(
            "Explicit governed preparation must not depend on model tool choice."
        )


class _ControlCenter:
    def snapshot(self, *, history_limit):
        return type("Snapshot", (), {"processes": (), "recent_runs": ()})()


class _ExecutionEvidenceControlCenter(_ControlCenter):
    def __init__(self) -> None:
        started = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)
        self.failed = WorkflowRun(
            execution_id="failed-run-1",
            workflow_name="Data Integration - Revenue_Load",
            status=WorkflowStatus.FAILED,
            started_at=started,
            completed_at=started + timedelta(seconds=20),
            error_message="Oracle load failed.",
            steps=(
                WorkflowStepResult(
                    name="Run Oracle job",
                    sequence=1,
                    status=WorkflowStepStatus.FAILED,
                    error_message="One record was rejected.",
                    details={
                        "job_id": 77,
                        "record_statistics": {
                            "records_read": 10,
                            "records_processed": 9,
                            "records_rejected": 1,
                            "details": [],
                        },
                    },
                ),
            ),
        )

    def list_workflow_runs(self, *, limit):
        assert limit == 100
        return (self.failed,)

    def get_workflow_run(self, execution_id):
        return self.failed if execution_id == self.failed.execution_id else None


class _DataReview:
    def list_cubes(self):
        return ()


class _SliceDataReview:
    def list_cubes(self):
        return (DataReviewCube("Plan1", "Plan1", 0, 4),)

    def list_dimensions(self, cube):
        assert cube == "Plan1"
        return (
            DimensionInfo("Account", "Account"),
            DimensionInfo("Period", "Period"),
        )
    def search_members(self, cube, dimension, *, query, limit):
        assert (cube, dimension, query, limit) == (
            "Plan1",
            "Account",
            "rev",
            10,
        )
        return DataReviewMemberSearch(
            cube="Plan1",
            dimension="Account",
            query="rev",
            members=(MemberInfo("Revenue"),),
            total_matches=1,
            has_more=False,
            limit=10,
        )

    def load_slice(self, selection):
        assert selection.cube == "Plan1"
        return DataReviewGrid(
            cube="Plan1",
            form_name="Plan1 data slice",
            grid=FormGrid(
                row_dimensions=("Account",),
                column_dimensions=("Period",),
                columns=(("Jan",),),
                rows=(FormGridRow(("Revenue",), (100,)),),
                pov=(("Scenario", "Forecast"),),
            ),
            row_count=1,
            column_count=1,
            cell_count=1,
            missing_cell_count=0,
        )

    def compare_slices(
        self,
        source,
        target,
        *,
        tolerance,
        max_mismatches,
        include_cells,
    ):
        assert source.cube == "Plan1"
        assert target.cube == "Rpt"
        assert tolerance == 0
        assert max_mismatches == 100
        assert include_cells is False
        return DataReviewComparison(
            source_cube="Plan1",
            target_cube="Rpt",
            result=DataValidationResult(
                source_form="Plan1 source slice",
                target_form="Rpt target slice",
                compared_cells=1,
                matched_cells=0,
                mismatches=(
                    DataMismatch(
                        row_headers=("Revenue",),
                        column_headers=("Jan",),
                        source_value=100,
                        target_value=99,
                        difference=1,
                    ),
                ),
                tolerance=0,
            ),
        )


class _SavedViewReportWorkspace:
    def catalog(self):
        return (
            ReportCatalogItem(
                name="revenue-forecast",
                title="Revenue Forecast",
                cube="Plan1",
                default_pov=(
                    ("Scenario", "Forecast"),
                    ("Product", "BaseData"),
                ),
                rows=(("Account", ("Revenue",)),),
                columns=(("Period", ("Jan",)),),
            ),
            ReportCatalogItem(
                name="legacy-layout",
                title="Legacy layout",
                cube="Plan1",
                default_pov=(),
            ),
        )


class _VarianceDataReview:
    def __init__(self, expected_product: str = "BaseData") -> None:
        self.expected_product = expected_product

    def compare_slices(
        self,
        source,
        target,
        *,
        tolerance,
        max_mismatches,
        include_cells,
    ):
        assert source.cube == target.cube == "Plan1"
        assert source.pov["Scenario"] == "Actual"
        assert target.pov["Scenario"] == "Budget"
        assert source.pov["Product"] == self.expected_product
        assert target.pov["Product"] == self.expected_product
        assert source.columns[0].dimension == "Period"
        assert source.columns[0].members == ("Sep",)
        assert target.columns[0].members == ("Sep",)
        assert tolerance == 1000
        assert max_mismatches == 100
        assert include_cells is False
        return DataReviewComparison(
            source_cube="Plan1",
            target_cube="Plan1",
            result=DataValidationResult(
                source_form="Actual Sep",
                target_form="Budget Sep",
                compared_cells=2,
                matched_cells=1,
                mismatches=(
                    DataMismatch(
                        row_headers=("Revenue",),
                        column_headers=("Sep",),
                        source_value=5000,
                        target_value=3500,
                        difference=1500,
                    ),
                ),
                tolerance=1000,
            ),
        )


class _SubstitutionVariables:
    def discover(self):
        return SubstitutionVariableCatalog(
            variables=(
                SubstitutionVariable(
                    name="CurYr",
                    value="FY26",
                    scope="ALL",
                ),
            ),
            plan_types=(),
            scopes=("ALL", "Plan1"),
        )


class _OperationCatalog:
    def discover_job_names(self, *, job_type):
        assert job_type == "RULES"
        return ("Revenue Forecast",)


class _NamedRuleCatalog:
    def __init__(self, *names: str) -> None:
        self._names = names

    def discover_job_names(self, *, job_type):
        assert job_type == "RULES"
        return self._names


class _DataMapCatalog:
    def discover_job_names(self, *, job_type):
        assert job_type == "PLAN_TYPE_MAP"
        return ("Revenue to Reporting",)


class _StandaloneFlowCatalog:
    def discover_job_names(self, *, job_type):
        return {
            "RULES": ("Revenue Forecast",),
            "PLAN_TYPE_MAP": ("Revenue to Reporting",),
        }.get(job_type, ())

    def discover_registered(self):
        return type("Catalog", (), {"pipelines": (), "data_integrations": ()})()


class _PipelineCatalog:
    def discover_registered(self):
        pipeline = type(
            "Pipeline",
            (),
            {
                "code": "PIPE01",
                "name": "Monthly Revenue Forecast",
                "description": "Load forecast data and calculate revenue",
            },
        )()
        return type(
            "Catalog",
            (),
            {"pipelines": (pipeline,), "data_integrations": ()},
        )()

    def preflight_pipeline(self, pipeline_code):
        assert pipeline_code == "PIPE01"
        return PipelineOperationPreview(
            code="PIPE01",
            display_name="Monthly Forecast",
            variables=(
                PipelineVariablePreview(
                    name="YEAR",
                    display_name="Planning Year",
                    default_value="FY27",
                    required=True,
                    editable=True,
                ),
            ),
            file_requirements=(
                PipelineFilePreview(
                    key="DataLoad_File",
                    display_name="Forecast data",
                    configured_reference=None,
                    required=True,
                    allowed_extensions=(".csv",),
                    consumers=("Load forecast",),
                ),
            ),
            stages=(
                PipelineStagePreview(
                    name="LOAD",
                    display_name="Load forecast",
                    job_count=1,
                    runs_in_parallel=False,
                ),
            ),
        )


class _PipelineAndRuleCatalog(_PipelineCatalog):
    def discover_job_names(self, *, job_type):
        return {"RULES": ("Revenue Forecast",)}.get(job_type, ())


def test_multi_step_planner_resolves_one_live_oracle_pipeline(
    tmp_path: Path,
) -> None:
    gateway = AgentCapabilityGateway(
        _settings(tmp_path),
        control_center=_ControlCenter(),
        data_review=_DataReview(),
        operation_catalog=_PipelineCatalog(),
    )

    result = gateway.execute(
        AgentToolCall(
            name="plan_multi_step_request",
            arguments={
                "objective": "Load forecast data and calculate the revenue forecast.",
                "requested_steps": ["data-import", "business-rules"],
            },
        )
    )

    assert result["resolution"] == "ORACLE_PIPELINE"
    assert result["executable"] is True
    assert result["pipeline"]["code"] == "PIPE01"
    assert result["pipeline"]["stages"][0]["display_name"] == "Load forecast"
    assert result["requested_steps"][0]["code"] == "data-import"


def test_multi_step_planner_never_chains_operations_without_pipeline_match(
    tmp_path: Path,
) -> None:
    class UnrelatedPipelineCatalog(_PipelineCatalog):
        def preflight_pipeline(self, pipeline_code):
            assert pipeline_code == "PIPE01"
            return PipelineOperationPreview(
                code="PIPE01",
                display_name="Archive Maintenance",
                variables=(),
                file_requirements=(),
                stages=(
                    PipelineStagePreview(
                        name="ARCHIVE",
                        display_name="Archive old audit files",
                        job_count=1,
                        runs_in_parallel=False,
                    ),
                ),
            )

    gateway = AgentCapabilityGateway(
        _settings(tmp_path),
        control_center=_ControlCenter(),
        data_review=_DataReview(),
        operation_catalog=UnrelatedPipelineCatalog(),
    )

    result = gateway.execute(
        AgentToolCall(
            name="plan_multi_step_request",
            arguments={
                "objective": "Import metadata, load data, calculate, and push to reporting.",
                "requested_steps": [
                    "metadata-import",
                    "data-import",
                    "business-rules",
                    "data-maps",
                ],
            },
        )
    )

    assert result["resolution"] == "NON_EXECUTABLE_PLAN"
    assert result["executable"] is False
    assert result["pipeline"] is None
    assert [item["code"] for item in result["requested_steps"]] == [
        "metadata-import",
        "data-import",
        "business-rules",
        "data-maps",
    ]


class _DataIntegrationCatalog:
    def discover_registered(self):
        integration = type(
            "Integration",
            (),
            {"name": "Revenue_Load"},
        )()
        return type(
            "Catalog",
            (),
            {"pipelines": (), "data_integrations": (integration,)},
        )()


class _DataImportCatalog:
    def discover_job_names(self, *, job_type):
        assert job_type == "IMPORT_DATA"
        return ("Import Forecast Data",)


class _MetadataImportCatalog:
    def discover_job_names(self, *, job_type):
        if job_type == "IMPORT_METADATA":
            return ("Import Products",)
        if job_type == "CUBE_REFRESH":
            return ("Refresh_Cube",)
        raise AssertionError(f"Unexpected job type: {job_type}")


class _DualLoadCatalog:
    def discover_job_names(self, *, job_type):
        return {
            "IMPORT_DATA": ("Import Actuals", "Import Forecast"),
            "IMPORT_METADATA": ("Import Products",),
            "CUBE_REFRESH": (),
        }.get(job_type, ())

    def discover_registered(self):
        integrations = tuple(
            type("Integration", (), {"name": name})()
            for name in ("Actual_Load", "Product_Metadata")
        )
        return type(
            "Catalog",
            (),
            {"pipelines": (), "data_integrations": integrations},
        )()


class _CubeRefreshCatalog:
    def discover_job_names(self, *, job_type):
        assert job_type == "CUBE_REFRESH"
        return ("Refresh_Cube",)


class _AgentRecoveryCatalog:
    def __init__(self) -> None:
        self.synced = False
        self.registered = False

    def discover_registered(self):
        pipelines = ()
        if self.synced or self.registered:
            pipelines = (
                type(
                    "Pipeline",
                    (),
                    {"code": "PIPE_NEW", "name": "Monthly Revenue Pipeline"},
                )(),
            )
        return type(
            "Catalog",
            (),
            {"pipelines": pipelines, "data_integrations": ()},
        )()

    def synchronize_artifacts(self):
        self.synced = True

    def register_pipeline(self, pipeline_code):
        assert pipeline_code == "PIPE_NEW"
        self.registered = True
        return type("Preview", (), {"code": "PIPE_NEW"})()


class _AgentRecoveryGraph:
    def __init__(self) -> None:
        self.pending = AgentClarificationRequest(
            request_id="recovery-1",
            operation_code="pipelines",
            display_name="Pipelines",
            prompt="Choose a Pipeline.",
            options=(),
            catalog_recovery={
                "enabled": True,
                "registration_mode": "verified",
            },
            search_context="monthly revenue pipeline",
        )
        self.selected = None

    def pending_clarification(self, **_):
        return self.pending

    def resume_clarification(self, *, value, **_):
        self.selected = value
        return AgentProviderResult(
            text="",
            input_request=AgentInputRequest(
                request_id="input-1",
                operation_code="pipelines",
                display_name="Pipelines",
                artifact_name=value,
                title="Review Pipeline inputs",
                description="Live inputs",
                fields=({"key": "pipeline_review"},),
            ),
        )


class _InputCancellationGraph:
    def __init__(self) -> None:
        self.values = "not-called"

    def resume_input(self, *, values, **_):
        self.values = values
        return AgentProviderResult(text="Preparation cancelled.")


class _ReportWorkspace:
    def catalog(self):
        return ()


class _ApprovedOperationGraph:
    request_id = "approval-1"

    def pending_approval(self, **_):
        result = self.resume_approval()
        payload = result.tool_activity[0].result["action_draft"]
        return AgentApprovalRequest(
            request_id=self.request_id,
            operation_code=payload["target_code"],
            display_name=payload["display_name"],
            objective=payload["objective"],
            artifact_name=payload["artifact_name"],
            category=payload["category"],
            risk_level=payload["risk_level"],
            route=payload["route"],
            input_values=payload["input_values"],
        )


class _ApprovedRuleGraph(_ApprovedOperationGraph):
    def resume_approval(self, **_):
        return AgentProviderResult(
            text="Approved.",
            tool_activity=(
                AgentToolActivity(
                    name="prepare_operation_action",
                    arguments={"operation_code": "business-rules"},
                    status="SUCCESS",
                    summary="Approved Business Rule inputs resolved.",
                    result={
                        "action_draft": {
                            "action_type": "operation",
                            "target_code": "business-rules",
                            "display_name": "Business Rules",
                            "category": "Calculation",
                            "risk_level": "Controlled",
                            "route": "/app/operations/business-rules",
                            "objective": "Calculate revenue.",
                            "artifact_name": "Revenue Forecast",
                            "required_inputs": [],
                            "stages": [],
                            "approval_required": True,
                            "status": "PREPARED_NOT_EXECUTED",
                            "input_schema": [],
                            "input_values": {
                                "runtime_prompts": {"Year": "FY27"}
                            },
                        }
                    },
                ),
            ),
        )


class _ApprovedDataMapGraph(_ApprovedOperationGraph):
    request_id = "approval-map-1"

    def resume_approval(self, **_):
        return AgentProviderResult(
            text="Approved.",
            tool_activity=(
                AgentToolActivity(
                    name="prepare_operation_action",
                    arguments={"operation_code": "data-maps"},
                    status="SUCCESS",
                    summary="Approved Data Map inputs resolved.",
                    result={
                        "action_draft": {
                            "action_type": "operation",
                            "target_code": "data-maps",
                            "display_name": "Data Maps",
                            "category": "Data movement",
                            "risk_level": "Elevated",
                            "route": "/app/operations/data-maps",
                            "objective": "Publish approved revenue.",
                            "artifact_name": "Revenue to Reporting",
                            "required_inputs": [],
                            "stages": [],
                            "approval_required": True,
                            "status": "PREPARED_NOT_EXECUTED",
                            "input_schema": [],
                            "input_values": {
                                "clear_target": False,
                                "member_overrides": {"Year": "FY27"},
                                "exclusion_overrides": {"Entity": "No Entity"},
                            },
                        }
                    },
                ),
            ),
        )


class _ApprovedPipelineGraph(_ApprovedOperationGraph):
    request_id = "approval-pipeline-1"

    def resume_approval(self, **_):
        return AgentProviderResult(
            text="Approved.",
            tool_activity=(
                AgentToolActivity(
                    name="prepare_operation_action",
                    arguments={"operation_code": "pipelines"},
                    status="SUCCESS",
                    summary="Approved Pipeline inputs resolved.",
                    result={
                        "action_draft": {
                            "action_type": "operation",
                            "target_code": "pipelines",
                            "display_name": "Pipelines",
                            "category": "Orchestration",
                            "risk_level": "Elevated",
                            "route": "/app/operations/pipelines",
                            "objective": "Run monthly forecast.",
                            "artifact_name": "PIPE01",
                            "required_inputs": [],
                            "stages": [],
                            "approval_required": True,
                            "status": "PREPARED_NOT_EXECUTED",
                            "input_schema": [],
                            "input_values": {
                                "runtime_variables": {"YEAR": "FY27"},
                                "uploads": {
                                    "DataLoad_File": "upload-token-1"
                                },
                                "upload_names": {
                                    "DataLoad_File": "Forecast.csv"
                                },
                                "inbox_files": {},
                                "configured_files": {},
                            },
                        }
                    },
                ),
            ),
        )


class _ApprovedDataIntegrationGraph(_ApprovedOperationGraph):
    request_id = "approval-integration-1"

    def resume_approval(self, **_):
        return AgentProviderResult(
            text="Approved.",
            tool_activity=(
                AgentToolActivity(
                    name="prepare_operation_action",
                    arguments={"operation_code": "data-integrations"},
                    status="SUCCESS",
                    summary="Approved Data Integration inputs resolved.",
                    result={
                        "action_draft": {
                            "action_type": "operation",
                            "target_code": "data-integrations",
                            "display_name": "Data Integrations",
                            "category": "Data loading",
                            "risk_level": "Elevated",
                            "route": "/app/operations/data-integrations",
                            "objective": "Load monthly revenue data.",
                            "artifact_name": "Revenue_Load",
                            "required_inputs": [],
                            "stages": [],
                            "approval_required": True,
                            "status": "PREPARED_NOT_EXECUTED",
                            "input_schema": [],
                            "input_values": {
                                "start_period": "Jan-27",
                                "end_period": "Mar-27",
                                "import_mode": "Replace",
                                "export_mode": "Merge",
                                "file_source": "Upload on governed screen",
                                "inbox_file": "",
                                "upload_token": "integration-upload-1",
                                "upload_name": "Revenue.csv",
                            },
                        }
                    },
                ),
            ),
        )


class _ApprovedDataImportGraph(_ApprovedOperationGraph):
    request_id = "approval-data-import-1"

    def resume_approval(self, **_):
        return AgentProviderResult(
            text="Approved.",
            tool_activity=(
                AgentToolActivity(
                    name="prepare_operation_action",
                    arguments={"operation_code": "data-import"},
                    status="SUCCESS",
                    summary="Approved Planning Data Import inputs resolved.",
                    result={
                        "action_draft": {
                            "action_type": "operation",
                            "target_code": "data-import",
                            "display_name": "Planning Data Import",
                            "category": "Data loading",
                            "risk_level": "Elevated",
                            "route": "/app/operations/data-import",
                            "objective": "Load the approved forecast file.",
                            "artifact_name": "Import Forecast Data",
                            "required_inputs": [],
                            "stages": [],
                            "approval_required": True,
                            "status": "PREPARED_NOT_EXECUTED",
                            "input_schema": [],
                            "input_values": {
                                "file_source": "Upload on governed screen",
                                "inbox_file": "",
                                "upload_token": "data-import-upload-1",
                                "upload_name": "Forecast.csv",
                                "error_file_name": "Forecast_Errors.log",
                            },
                        }
                    },
                ),
            ),
        )


class _ApprovedMetadataImportGraph(_ApprovedOperationGraph):
    request_id = "approval-metadata-import-1"

    def resume_approval(self, **_):
        return AgentProviderResult(
            text="Approved.",
            tool_activity=(
                AgentToolActivity(
                    name="prepare_operation_action",
                    arguments={"operation_code": "metadata-import"},
                    status="SUCCESS",
                    summary="Approved Metadata Import inputs resolved.",
                    result={
                        "action_draft": {
                            "action_type": "operation",
                            "target_code": "metadata-import",
                            "display_name": "Metadata Import",
                            "category": "Application administration",
                            "risk_level": "Elevated",
                            "route": "/app/operations/metadata-import",
                            "objective": "Import the approved product hierarchy.",
                            "artifact_name": "Import Products",
                            "required_inputs": [],
                            "stages": [],
                            "approval_required": True,
                            "status": "PREPARED_NOT_EXECUTED",
                            "input_schema": [],
                            "input_values": {
                                "file_source": "Upload on governed screen",
                                "inbox_file": "",
                                "upload_token": "metadata-upload-1",
                                "upload_name": "Products.csv",
                                "error_file_name": "Metadata_Errors.csv",
                                "refresh_after_import": True,
                                "refresh_job_name": "Refresh_Cube",
                            },
                        }
                    },
                ),
            ),
        )


class _ApprovedCubeRefreshGraph(_ApprovedOperationGraph):
    request_id = "approval-cube-refresh-1"

    def resume_approval(self, **_):
        return AgentProviderResult(
            text="Approved.",
            tool_activity=(
                AgentToolActivity(
                    name="prepare_operation_action",
                    arguments={"operation_code": "cube-refresh"},
                    status="SUCCESS",
                    summary="Approved Cube Refresh job resolved.",
                    result={
                        "action_draft": {
                            "action_type": "operation",
                            "target_code": "cube-refresh",
                            "display_name": "Planning Cube Refresh",
                            "category": "Application administration",
                            "risk_level": "Elevated",
                            "route": "/app/operations/cube-refresh",
                            "objective": "Synchronize Planning metadata.",
                            "artifact_name": "Refresh_Cube",
                            "required_inputs": [],
                            "stages": [],
                            "approval_required": True,
                            "status": "PREPARED_NOT_EXECUTED",
                            "input_schema": [],
                            "input_values": {},
                        }
                    },
                ),
            ),
        )


class _ApprovedSubstitutionVariableGraph(_ApprovedOperationGraph):
    request_id = "approval-variable-1"

    def resume_approval(self, **_):
        return AgentProviderResult(
            text="Approved.",
            tool_activity=(
                AgentToolActivity(
                    name="prepare_operation_action",
                    arguments={"operation_code": "substitution-variables"},
                    status="SUCCESS",
                    summary="Approved substitution-variable change resolved.",
                    result={
                        "action_draft": {
                            "action_type": "operation",
                            "target_code": "substitution-variables",
                            "display_name": "Substitution Variables",
                            "category": "Application administration",
                            "risk_level": "Elevated",
                            "route": "/app/operations/substitution-variables",
                            "objective": "Move the current Planning year.",
                            "artifact_name": "CurYr",
                            "required_inputs": [],
                            "stages": [],
                            "approval_required": True,
                            "status": "PREPARED_NOT_EXECUTED",
                            "input_schema": [],
                            "input_values": {
                                "action": "UPDATE",
                                "scope": "ALL",
                                "variable_name": "CurYr",
                                "new_value": "FY27",
                                "expected_current_value": "FY26",
                                "create_if_missing": False,
                            },
                        }
                    },
                ),
            ),
        )


class _OperationManager:
    def __init__(self) -> None:
        self.submissions = []
        self.cleanups = []

    def submit(self, operation_input, *, actor, cleanup=None):
        self.submissions.append((operation_input, actor))
        self.cleanups.append(cleanup)
        target_name = getattr(
            operation_input,
            "rule_name",
            getattr(
                operation_input,
                "data_map_name",
                getattr(
                    operation_input,
                    "pipeline_code",
                    getattr(
                        operation_input,
                        "integration_name",
                        getattr(
                            operation_input,
                            "job_name",
                            (
                                f"{operation_input.scope}.{operation_input.name}"
                                if hasattr(operation_input, "scope")
                                else "unknown"
                            ),
                        ),
                    ),
                ),
            ),
        )
        return SimpleNamespace(
            execution_id="operation-execution-1",
            target_name=target_name,
            status=SimpleNamespace(value="QUEUED"),
        )

    def submit_flow(self, flow_input, *, actor, cleanup=None):
        self.submissions.append((flow_input, actor))
        self.cleanups.append(cleanup)
        return SimpleNamespace(
            execution_id="flow-execution-1",
            target_name=flow_input.name,
            status=SimpleNamespace(value="QUEUED"),
        )


class _ActiveFlowOperationManager:
    def __init__(self) -> None:
        self.stop_requests: list[tuple[str, str]] = []

    def get(self, execution_id: str):
        if execution_id != "active-flow-1":
            return None
        return SimpleNamespace(status=SimpleNamespace(value="RUNNING"))

    def request_flow_stop(self, execution_id: str, *, requested_by: str):
        self.stop_requests.append((execution_id, requested_by))
        return SimpleNamespace(
            execution_id=execution_id,
            target_name="September Close",
            status=SimpleNamespace(value="RUNNING"),
        )


def test_agent_service_persists_provider_neutral_conversation(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    access = AccessControlService(settings.workflow_database_file)
    access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    repository = SQLiteAgentRepository(settings.workflow_database_file)
    gateway = AgentCapabilityGateway(
        settings,
        control_center=_ControlCenter(),
        data_review=_DataReview(),
    )
    service = AgentApplicationService(
        settings,
        gateway=gateway,
        repository=repository,
        provider_factory=_FakeProvider,
    )
    user = _user()

    conversation = service.create_conversation(user)
    result = service.send_message(
        conversation_id=conversation.conversation_id,
        user=user,
        content="What can I run?",
    )

    messages = service.get_messages(conversation.conversation_id, user)
    assert [item.role.value for item in messages] == ["user", "assistant"]
    assert result["message"].content.startswith("The configured operations")
    assert service.list_conversations(user)[0].title == "What can I run?"


def test_agent_safe_stop_targets_latest_active_standalone_flow(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    AccessControlService(settings.workflow_database_file).bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    repository = SQLiteAgentRepository(settings.workflow_database_file)
    user = _user()
    conversation = repository.create_conversation(
        user_id=user.user_id,
        provider="gemini",
        model="test-model",
    )
    repository.reserve_action_decision(
        request_id="run-active-flow",
        conversation_id=conversation.conversation_id,
        user_id=user.user_id,
        username=user.username,
        operation_code="standalone-flow",
        artifact_name="September Close",
        decision="APPROVE",
        payload_checksum="a" * 64,
        payload_snapshot={"name": "September Close"},
    )
    repository.finalize_action_decision(
        request_id="run-active-flow",
        user_id=user.user_id,
        conversation_id=conversation.conversation_id,
        outcome_status="SUBMITTED",
        execution_id="active-flow-1",
    )
    manager = _ActiveFlowOperationManager()
    service = AgentApplicationService(
        settings,
        gateway=AgentCapabilityGateway(
            settings,
            control_center=_ControlCenter(),
            data_review=_DataReview(),
        ),
        repository=repository,
        operation_manager=manager,
    )

    result = service.send_message(
        conversation_id=conversation.conversation_id,
        user=user,
        content="Stop after this step.",
    )

    assert manager.stop_requests == [("active-flow-1", "planner")]
    assert result["execution"] == {
        "execution_id": "active-flow-1",
        "operation_code": "standalone-flow",
        "target_name": "September Close",
        "status": "RUNNING",
    }
    assert "no later flow step will start" in result["message"].content


def test_agent_conversations_are_scoped_to_their_owner(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    access = AccessControlService(settings.workflow_database_file)
    access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    repository = SQLiteAgentRepository(settings.workflow_database_file)
    conversation = repository.create_conversation(
        user_id=1,
        provider="gemini",
        model="test-model",
    )

    with pytest.raises(AgentConversationError):
        repository.list_messages(conversation.conversation_id, user_id=2)


def test_agent_repository_restores_latest_validated_review_selection(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    AccessControlService(settings.workflow_database_file).bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    repository = SQLiteAgentRepository(settings.workflow_database_file)
    conversation = repository.create_conversation(
        user_id=1,
        provider="gemini",
        model="test-model",
    )
    selection = {
        "cube": "Plan1",
        "pov": [{"dimension": "Year", "member": "FY27"}],
        "rows": [{"dimension": "Account", "members": ["Revenue"]}],
        "columns": [{"dimension": "Period", "members": ["Jan"]}],
    }
    repository.record_tool_activity(
        conversation_id=conversation.conversation_id,
        user_id=1,
        activities=(
            AgentToolActivity(
                name="review_data_slice",
                arguments=selection,
                status="SUCCESS",
                summary="Live slice returned.",
            ),
        ),
    )

    activity = repository.latest_successful_tool_activity(
        conversation_id=conversation.conversation_id,
        user_id=1,
        tool_names=("review_data_slice", "compare_data_slices"),
    )

    assert activity is not None
    assert activity.name == "review_data_slice"
    assert activity.arguments == selection


def test_agent_service_restores_saved_data_explorer_view_as_exact_context(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    AccessControlService(settings.workflow_database_file).bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    repository = SQLiteAgentRepository(settings.workflow_database_file)
    conversation = repository.create_conversation(
        user_id=1,
        provider="gemini",
        model="test-model",
    )
    repository.record_tool_activity(
        conversation_id=conversation.conversation_id,
        user_id=1,
        activities=(
            AgentToolActivity(
                name="review_saved_data_view",
                arguments={"name": "revenue-forecast"},
                status="SUCCESS",
                summary="Saved view loaded.",
            ),
        ),
    )
    gateway = AgentCapabilityGateway(
        settings,
        control_center=_ControlCenter(),
        data_review=_SliceDataReview(),
        report_workspace=_SavedViewReportWorkspace(),
    )
    service = AgentApplicationService(
        settings,
        gateway=gateway,
        repository=repository,
        provider_factory=_FakeProvider,
    )

    context = service.get_data_review_context(
        conversation.conversation_id,
        _user(Permission.DATA_REVIEW),
    )

    assert context == {
        "tool": "review_saved_data_view",
        "selection": {
            "cube": "Plan1",
            "pov": {"Scenario": "Forecast", "Product": "BaseData"},
            "rows": [{"dimension": "Account", "members": ["Revenue"]}],
            "columns": [{"dimension": "Period", "members": ["Jan"]}],
        },
    }

    variance_arguments = {
        "name": "revenue-forecast",
        "comparison": "Actual vs Budget",
        "period": "Sep",
        "year": "FY26",
        "threshold": 500,
        "pov_overrides": {"Product": "Snacks"},
    }
    repository.record_tool_activity(
        conversation_id=conversation.conversation_id,
        user_id=1,
        activities=(
            AgentToolActivity(
                name="review_saved_variance",
                arguments=variance_arguments,
                status="SUCCESS",
                summary="Variance compared.",
            ),
        ),
    )

    assert service.get_data_review_context(
        conversation.conversation_id,
        _user(Permission.DATA_REVIEW),
    ) == {
        "tool": "review_saved_variance",
        "selection": variance_arguments,
    }


def test_agent_service_preserves_null_guided_input_as_cancel(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    access = AccessControlService(settings.workflow_database_file)
    account = access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    repository = SQLiteAgentRepository(settings.workflow_database_file)
    gateway = AgentCapabilityGateway(
        settings,
        control_center=_ControlCenter(),
        data_review=_DataReview(),
    )
    graph = _InputCancellationGraph()
    service = AgentApplicationService(
        settings,
        gateway=gateway,
        repository=repository,
        graph_orchestrator=graph,
    )
    user = replace(
        _user(Permission.OPERATION_EXECUTE),
        user_id=account.user_id,
    )
    conversation = service.create_conversation(user)

    result = service.resolve_input(
        conversation_id=conversation.conversation_id,
        user=user,
        request_id="input-1",
        values=None,
    )

    assert graph.values is None
    assert result["message"].content == "Preparation cancelled."


def test_action_preparation_uses_canonical_operation_governance(
    tmp_path: Path,
) -> None:
    gateway = AgentCapabilityGateway(
        _settings(tmp_path),
        control_center=_ControlCenter(),
        data_review=_DataReview(),
    )

    result = gateway.execute(
        AgentToolCall(
            name="prepare_operation_action",
            arguments={
                "operation_code": "data-maps",
                "objective": "Push the approved forecast to reporting.",
                "artifact_name": "Product_Revenue_to_Reporting",
            },
        )
    )
    draft = result["action_draft"]

    assert draft["display_name"] == "Data Maps"
    assert draft["route"] == "/app/operations/data-maps"
    assert draft["risk_level"] == "Elevated"
    assert draft["status"] == "PREPARED_NOT_EXECUTED"
    assert draft["approval_required"] is True

    with pytest.raises(AgentCapabilityError):
        gateway.execute(
            AgentToolCall(
                name="prepare_operation_action",
                arguments={
                    "operation_code": "invented-operation",
                    "objective": "Do something unsupported.",
                },
            )
        )


def test_agent_execution_evidence_tool_supports_latest_failed_selector(
    tmp_path: Path,
) -> None:
    gateway = AgentCapabilityGateway(
        _settings(tmp_path),
        control_center=_ExecutionEvidenceControlCenter(),
        data_review=_DataReview(),
    )

    result = gateway.execute(
        AgentToolCall(
            name="get_execution_evidence",
            arguments={"selector": "latest_failed"},
        )
    )

    execution = result["execution"]
    assert execution["execution_id"] == "failed-run-1"
    assert execution["record_statistics"]["records_rejected"] == 1
    assert execution["diagnosis"].startswith("Failed at Run Oracle job")

    with pytest.raises(AgentCapabilityError, match="not both"):
        gateway.execute(
            AgentToolCall(
                name="get_execution_evidence",
                arguments={
                    "execution_id": "failed-run-1",
                    "selector": "latest_failed",
                },
            )
        )


def test_agent_data_review_tools_query_live_slice_and_comparison(
    tmp_path: Path,
) -> None:
    gateway = AgentCapabilityGateway(
        _settings(tmp_path),
        control_center=_ControlCenter(),
        data_review=_SliceDataReview(),
    )

    dimensions = gateway.execute(
        AgentToolCall(
            name="list_cube_dimensions",
            arguments={"cube": "Plan1"},
        )
    )
    members = gateway.execute(
        AgentToolCall(
            name="search_dimension_members",
            arguments={
                "cube": "Plan1",
                "dimension": "Account",
                "query": "rev",
                "limit": 10,
            },
        )
    )
    source = {
        "cube": "Plan1",
        "pov": [{"dimension": "Scenario", "member": "Forecast"}],
        "rows": [{"dimension": "Account", "members": ["Revenue"]}],
        "columns": [{"dimension": "Period", "members": ["Jan"]}],
    }
    reviewed = gateway.execute(
        AgentToolCall(name="review_data_slice", arguments=source)
    )
    compared = gateway.execute(
        AgentToolCall(
            name="compare_data_slices",
            arguments={
                "source": source,
                "target": {**source, "cube": "Rpt"},
            },
        )
    )

    assert dimensions["dimensions"][0]["name"] == "Account"
    assert members["members"][0]["name"] == "Revenue"
    assert reviewed["grid"]["rows"][0]["data"] == (100,)
    assert reviewed["truncated"] is False
    assert compared["result"]["matched_cells"] == 0


def test_agent_data_explorer_saved_view_uses_server_side_layout(
    tmp_path: Path,
) -> None:
    gateway = AgentCapabilityGateway(
        _settings(tmp_path),
        control_center=_ControlCenter(),
        data_review=_SliceDataReview(),
        report_workspace=_SavedViewReportWorkspace(),
    )

    catalog = gateway.execute(
        AgentToolCall(name="list_data_explorer_views", arguments={})
    )
    reviewed = gateway.execute(
        AgentToolCall(
            name="review_saved_data_view",
            arguments={"name": "Revenue-Forecast"},
        )
    )

    assert catalog["total_count"] == 1
    assert catalog["views"][0]["name"] == "revenue-forecast"
    assert reviewed["saved_view"] == {
        "name": "revenue-forecast",
        "title": "Revenue Forecast",
    }
    assert reviewed["request"] == {
        "cube": "Plan1",
        "pov": {"Scenario": "Forecast", "Product": "BaseData"},
        "rows": [{"dimension": "Account", "members": ["Revenue"]}],
        "columns": [{"dimension": "Period", "members": ["Jan"]}],
    }

    with pytest.raises(AgentCapabilityError, match="was not found"):
        gateway.execute(
            AgentToolCall(
                name="review_saved_data_view",
                arguments={"name": "missing"},
            )
        )


def test_agent_variance_review_reuses_saved_layout_and_live_values(
    tmp_path: Path,
) -> None:
    gateway = AgentCapabilityGateway(
        _settings(tmp_path),
        control_center=_ControlCenter(),
        data_review=_VarianceDataReview(expected_product="Snacks"),
        report_workspace=_SavedViewReportWorkspace(),
    )

    catalog = gateway.execute(
        AgentToolCall(
            name="list_variance_views",
            arguments={
                "comparison": "Actual vs Budget",
                "period": "Sep",
                "threshold": 1000,
            },
        )
    )
    compared = gateway.execute(
        AgentToolCall(
            name="review_saved_variance",
            arguments={
                "name": "revenue-forecast",
                "comparison": "Actual vs Budget",
                "period": "Sep",
                "threshold": 1000,
                "pov_overrides": {"Product": "Snacks"},
            },
        )
    )

    assert catalog["purpose"] == "variance"
    assert catalog["comparison"] == "Actual vs Budget"
    assert catalog["views"][0]["name"] == "revenue-forecast"
    assert compared["saved_view"]["name"] == "revenue-forecast"
    assert compared["variance_context"] == {
        "comparison": "Actual vs Budget",
        "period": "Sep",
        "year": "",
        "threshold": 1000.0,
        "pov_overrides": {"Product": "Snacks"},
    }
    assert compared["result"]["compared_cells"] == 2
    assert compared["result"]["matched_cells"] == 1


def test_agent_service_persists_action_draft_for_conversation(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    access = AccessControlService(settings.workflow_database_file)
    access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    repository = SQLiteAgentRepository(settings.workflow_database_file)
    gateway = AgentCapabilityGateway(
        settings,
        control_center=_ControlCenter(),
        data_review=_DataReview(),
    )
    service = AgentApplicationService(
        settings,
        gateway=gateway,
        repository=repository,
        provider_factory=_DraftProvider,
    )
    user = _user(Permission.OPERATION_EXECUTE)
    conversation = service.create_conversation(user)

    result = service.send_message(
        conversation_id=conversation.conversation_id,
        user=user,
        content="Prepare the revenue forecast rule.",
    )
    persisted = service.get_action_drafts(
        conversation.conversation_id, user
    )

    assert len(result["action_drafts"]) == 1
    assert persisted == result["action_drafts"]
    assert persisted[0].artifact_name == "Revenue Forecast"
    assert persisted[0].message_id == result["message"].message_id


def test_metadata_dimension_request_discovers_both_load_routes(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    access = AccessControlService(settings.workflow_database_file)
    account = access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    repository = SQLiteAgentRepository(settings.workflow_database_file)
    service = AgentApplicationService(
        settings,
        gateway=AgentCapabilityGateway(
            settings,
            control_center=_ControlCenter(),
            data_review=_DataReview(),
            operation_catalog=_DualLoadCatalog(),
        ),
        repository=repository,
        provider_factory=_NeverCalledProvider,
    )
    user = replace(
        _user(Permission.OPERATION_EXECUTE),
        user_id=account.user_id,
    )
    conversation = service.create_conversation(user)

    initial = service.send_message(
        conversation_id=conversation.conversation_id,
        user=user,
        content="Load new product dimensions.",
    )
    choice = initial["clarification_request"]
    assert choice is not None
    assert choice.operation_code == "load-options"
    assert "metadata-import::Import Products" in choice.options
    assert "data-integrations::Product_Metadata" in choice.options
    assert initial["input_request"] is None
    assert initial["approval_request"] is None


def test_new_product_member_dialogue_keeps_context_without_model_reasking(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    access = AccessControlService(settings.workflow_database_file)
    account = access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    service = AgentApplicationService(
        settings,
        gateway=AgentCapabilityGateway(
            settings,
            control_center=_ControlCenter(),
            data_review=_DataReview(),
        ),
        repository=SQLiteAgentRepository(settings.workflow_database_file),
        provider_factory=_NeverCalledProvider,
    )
    user = replace(_user(Permission.OPERATION_EXECUTE), user_id=account.user_id)
    conversation = service.create_conversation(user)

    for prompt in (
        "Add Orange Juice as a new product",
        "Product Dimension",
        "P_TP",
        "P_TP is the name of a dimension member under Product",
        "already told you",
    ):
        result = service.send_message(
            conversation_id=conversation.conversation_id,
            user=user,
            content=prompt,
        )
        assert "Metadata Import" in result["message"].content
        assert (
            "Which metadata file" in result["message"].content
            or "Is P_TP its parent member?" in result["message"].content
        )
        assert "what task" not in result["message"].content.casefold()
        assert result["approval_request"] is None

    assert "P_TP" in result["message"].content


@pytest.mark.parametrize(
    ("prompt", "expected_job", "integration_name"),
    (
        (
            "Load data",
            "data-import::Import Actuals",
            "data-integrations::Actual_Load",
        ),
        (
            "Load January FY27 Actual data using Actual_Jan.csv",
            "data-import::Import Actuals",
            "data-integrations::Actual_Load",
        ),
        (
            "Load product units data from Jan to Mar for FY27",
            "data-import::Import Actuals",
            "data-integrations::Actual_Load",
        ),
        (
            "Load metadata",
            "metadata-import::Import Products",
            "data-integrations::Product_Metadata",
        ),
        (
            "Load Product metadata using Product.csv",
            "metadata-import::Import Products",
            "data-integrations::Product_Metadata",
        ),
    ),
)
def test_agent_compares_integration_and_saved_job_before_a_load(
    tmp_path: Path,
    prompt: str,
    expected_job: str,
    integration_name: str,
) -> None:
    settings = _settings(tmp_path)
    access = AccessControlService(settings.workflow_database_file)
    account = access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    service = AgentApplicationService(
        settings,
        gateway=AgentCapabilityGateway(
            settings,
            control_center=_ControlCenter(),
            data_review=_DataReview(),
            operation_catalog=_DualLoadCatalog(),
        ),
        repository=SQLiteAgentRepository(settings.workflow_database_file),
        provider_factory=_NeverCalledProvider,
    )
    user = replace(_user(Permission.OPERATION_EXECUTE), user_id=account.user_id)
    conversation = service.create_conversation(user)

    result = service.send_message(
        conversation_id=conversation.conversation_id,
        user=user,
        content=prompt,
    )

    choice = result["clarification_request"]
    assert choice is not None
    assert choice.operation_code == "load-options"
    assert expected_job in choice.options
    assert integration_name in choice.options
    assert result["approval_request"] is None
    if "metadata" in prompt.casefold():
        assert "purpose not verified" in choice.option_labels[integration_name]

    selected = service.resolve_clarification(
        conversation_id=conversation.conversation_id,
        user=user,
        request_id=choice.request_id,
        value=integration_name,
    )

    guided = selected["input_request"]
    assert guided is not None
    assert guided.operation_code == "data-integrations"
    assert guided.artifact_name == integration_name.partition("::")[2]
    if "Jan to Mar" in prompt:
        assert guided.context["prefill"] == {
            "year": "FY27",
            "start_month": "Jan",
            "end_month": "Mar",
        }
    if "metadata" in prompt.casefold():
        assert guided.context["export_modes"] == ["Merge"]
        assert "purpose" in guided.description

    job_conversation = service.create_conversation(user)
    job_result = service.send_message(
        conversation_id=job_conversation.conversation_id,
        user=user,
        content=prompt,
    )
    job_choice = job_result["clarification_request"]
    assert job_choice is not None
    selected_job = service.resolve_clarification(
        conversation_id=job_conversation.conversation_id,
        user=user,
        request_id=job_choice.request_id,
        value=expected_job,
    )
    job_input = selected_job["input_request"]
    assert job_input is not None
    assert job_input.operation_code == expected_job.partition("::")[0]
    assert job_input.artifact_name == expected_job.partition("::")[2]


def test_load_route_selection_overrides_artifact_named_in_prompt(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    access = AccessControlService(settings.workflow_database_file)
    account = access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    service = AgentApplicationService(
        settings,
        gateway=AgentCapabilityGateway(
            settings,
            control_center=_ControlCenter(),
            data_review=_DataReview(),
            operation_catalog=_DualLoadCatalog(),
        ),
        repository=SQLiteAgentRepository(settings.workflow_database_file),
        provider_factory=_NeverCalledProvider,
    )
    user = replace(_user(Permission.OPERATION_EXECUTE), user_id=account.user_id)
    conversation = service.create_conversation(user)
    result = service.send_message(
        conversation_id=conversation.conversation_id,
        user=user,
        content="Load data using Import Actuals",
    )
    choice = result["clarification_request"]
    assert choice is not None
    assert "data-import::Import Forecast" in choice.options

    selected = service.resolve_clarification(
        conversation_id=conversation.conversation_id,
        user=user,
        request_id=choice.request_id,
        value="data-import::Import Forecast",
    )
    guided = selected["input_request"]
    assert guided is not None
    assert guided.operation_code == "data-import"
    assert guided.artifact_name == "Import Forecast"


def test_agent_does_not_prepare_a_load_when_both_catalogs_are_empty(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    access = AccessControlService(settings.workflow_database_file)
    account = access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    service = AgentApplicationService(
        settings,
        gateway=AgentCapabilityGateway(
            settings,
            control_center=_ControlCenter(),
            data_review=_DataReview(),
        ),
        repository=SQLiteAgentRepository(settings.workflow_database_file),
        provider_factory=_NeverCalledProvider,
    )
    user = replace(_user(Permission.OPERATION_EXECUTE), user_id=account.user_id)
    conversation = service.create_conversation(user)

    result = service.send_message(
        conversation_id=conversation.conversation_id,
        user=user,
        content="Load Product metadata using Product.csv",
    )

    assert result["clarification_request"] is None
    assert result["approval_request"] is None
    assert "No operation was prepared" in result["message"].content


def test_agent_reports_authenticated_users_own_role_and_permissions(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    access = AccessControlService(settings.workflow_database_file)
    account = access.bootstrap_administrator(
        username="planner",
        display_name="Finance Planner",
        email=None,
        password="Strong password 123!",
    )
    repository = SQLiteAgentRepository(settings.workflow_database_file)
    service = AgentApplicationService(
        settings,
        gateway=AgentCapabilityGateway(
            settings,
            control_center=_ControlCenter(),
            data_review=_DataReview(),
        ),
        repository=repository,
        provider_factory=_NeverCalledProvider,
    )
    user = replace(
        _user(Permission.OPERATION_EXECUTE, Permission.DATA_REVIEW),
        user_id=account.user_id,
        username="planner",
        display_name="Finance Planner",
    )
    conversation = service.create_conversation(user)

    permissions = service.send_message(
        conversation_id=conversation.conversation_id,
        user=user,
        content="Hello, can you describe my allowed permisions?",
    )
    role = service.send_message(
        conversation_id=conversation.conversation_id,
        user=user,
        content="What is my role?",
    )

    for response in (permissions, role):
        content = response["message"].content
        assert "Finance Planner (`planner`)" in content
        assert "Platform role:** Power User" in content
        assert "operation.execute" in content
        assert "data.review" in content
        assert "governed operational assistant" not in content.casefold()
        assert response["tool_activity"][0].name == "get_current_user_access"
        assert response["tool_activity"][0].result["username"] == "planner"


def test_personal_access_detection_does_not_confuse_assistant_identity() -> None:
    assert AgentApplicationService._is_current_user_access_request(
        "Tell me my permissions, not yours."
    )
    assert AgentApplicationService._is_current_user_access_request(
        "What permissions do I have?"
    )
    assert not AgentApplicationService._is_current_user_access_request(
        "What is your role?"
    )
    assert not AgentApplicationService._is_current_user_access_request(
        "List the platform operations."
    )
    assert not AgentApplicationService._is_current_user_access_request(
        "What can I run?"
    )


def test_explicit_agent_approval_queues_rule_without_redundant_draft(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    access = AccessControlService(settings.workflow_database_file)
    access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    repository = SQLiteAgentRepository(settings.workflow_database_file)
    gateway = AgentCapabilityGateway(
        settings,
        control_center=_ControlCenter(),
        data_review=_DataReview(),
        operation_catalog=_OperationCatalog(),
    )
    manager = _OperationManager()
    service = AgentApplicationService(
        settings,
        gateway=gateway,
        repository=repository,
        graph_orchestrator=_ApprovedRuleGraph(),
        operation_manager=manager,
    )
    user = replace(
        _user(Permission.OPERATION_EXECUTE),
        user_id=access.list_users()[0].user_id,
    )
    conversation = service.create_conversation(user)

    result = service.resolve_approval(
        conversation_id=conversation.conversation_id,
        user=user,
        request_id="approval-1",
        decision="approve",
    )

    assert result["action_drafts"] == ()
    assert result["execution"] == {
        "execution_id": "operation-execution-1",
        "operation_code": "business-rules",
        "target_name": "Revenue Forecast",
        "status": "QUEUED",
    }
    operation_input, actor = manager.submissions[0]
    assert operation_input.rule_name == "Revenue Forecast"
    assert operation_input.runtime_prompts == {"Year": "FY27"}
    assert actor.trigger_source is TriggerSource.AI_AGENT
    assert service.get_action_drafts(conversation.conversation_id, user) == ()
    decision = result["decision"]
    assert decision.decision == "APPROVE"
    assert decision.outcome_status == "SUBMITTED"
    assert decision.execution_id == "operation-execution-1"
    assert len(decision.payload_checksum) == 64

    replayed = service.resolve_approval(
        conversation_id=conversation.conversation_id,
        user=user,
        request_id="approval-1",
        decision="approve",
    )

    assert replayed["execution"]["execution_id"] == "operation-execution-1"
    assert len(manager.submissions) == 1
    assert len(service.get_action_decisions(conversation.conversation_id, user)) == 1


def test_business_rule_request_runs_full_governed_flow_without_model_tool_choice(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    access = AccessControlService(settings.workflow_database_file)
    access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    repository = SQLiteAgentRepository(settings.workflow_database_file)
    gateway = AgentCapabilityGateway(
        settings,
        control_center=_ControlCenter(),
        data_review=_DataReview(),
        operation_catalog=_OperationCatalog(),
    )
    manager = _OperationManager()
    service = AgentApplicationService(
        settings,
        gateway=gateway,
        repository=repository,
        provider_factory=_NeverCalledProvider,
        operation_manager=manager,
    )
    user = replace(
        _user(Permission.OPERATION_EXECUTE),
        user_id=access.list_users()[0].user_id,
    )
    conversation = service.create_conversation(user)

    prepared = service.send_message(
        conversation_id=conversation.conversation_id,
        user=user,
        content="Run business rule Revenue Forecast.",
    )
    input_request = prepared["input_request"]
    assert input_request is not None
    assert input_request.artifact_name == "Revenue Forecast"

    reviewed = service.resolve_input(
        conversation_id=conversation.conversation_id,
        user=user,
        request_id=input_request.request_id,
        values={
            "runtime_prompt_mode": "Provide runtime prompt values",
            "runtime_prompts": {"Year": "FY27"},
        },
    )
    approval = reviewed["approval_request"]
    assert approval is not None
    assert approval.input_values == {
        "runtime_prompts": {"Year": "FY27"}
    }

    completed = service.resolve_approval(
        conversation_id=conversation.conversation_id,
        user=user,
        request_id=approval.request_id,
        decision="approve",
    )

    assert completed["execution"]["target_name"] == "Revenue Forecast"
    operation_input, actor = manager.submissions[0]
    assert operation_input.rule_name == "Revenue Forecast"
    assert operation_input.runtime_prompts == {"Year": "FY27"}
    assert actor.trigger_source is TriggerSource.AI_AGENT


@pytest.mark.parametrize(
    ("prompt", "rule_name"),
    (
        ("Run Aggregate Plan rule.", "Aggregate Plan"),
        (
            "run clear facilities allocation rule",
            "Clear Facilities Allocation",
        ),
    ),
)
def test_named_rule_request_does_not_require_business_rule_wording(
    tmp_path: Path,
    prompt: str,
    rule_name: str,
) -> None:
    settings = _settings(tmp_path)
    access = AccessControlService(settings.workflow_database_file)
    account = access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    repository = SQLiteAgentRepository(settings.workflow_database_file)
    service = AgentApplicationService(
        settings,
        gateway=AgentCapabilityGateway(
            settings,
            control_center=_ControlCenter(),
            data_review=_DataReview(),
            operation_catalog=_NamedRuleCatalog(rule_name),
        ),
        repository=repository,
        provider_factory=_NeverCalledProvider,
        operation_manager=_OperationManager(),
    )
    user = replace(
        _user(Permission.OPERATION_EXECUTE),
        user_id=account.user_id,
    )
    conversation = service.create_conversation(user)

    prepared = service.send_message(
        conversation_id=conversation.conversation_id,
        user=user,
        content=prompt,
    )

    assert prepared["input_request"] is not None
    assert prepared["input_request"].artifact_name == rule_name


@pytest.mark.parametrize(
    "prompt",
    (
        "Calculate product revenue",
        "Run revenue calculation",
        "Please compute product revenue",
    ),
)
def test_calculation_request_discovers_live_business_rules_without_model(
    tmp_path: Path,
    prompt: str,
) -> None:
    settings = _settings(tmp_path)
    access = AccessControlService(settings.workflow_database_file)
    account = access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    service = AgentApplicationService(
        settings,
        gateway=AgentCapabilityGateway(
            settings,
            control_center=_ControlCenter(),
            data_review=_DataReview(),
            operation_catalog=_NamedRuleCatalog(
                "Product Revenue Rule", "Gross Margin Calc"
            ),
        ),
        repository=SQLiteAgentRepository(settings.workflow_database_file),
        provider_factory=_NeverCalledProvider,
    )
    user = replace(_user(Permission.OPERATION_EXECUTE), user_id=account.user_id)
    conversation = service.create_conversation(user)

    prepared = service.send_message(
        conversation_id=conversation.conversation_id,
        user=user,
        content=prompt,
    )

    choice = prepared["clarification_request"]
    assert choice is not None
    assert choice.operation_code == "business-rules"
    assert set(choice.options) == {"Product Revenue Rule", "Gross Margin Calc"}
    assert prepared["approval_request"] is None

    selected = service.resolve_clarification(
        conversation_id=conversation.conversation_id,
        user=user,
        request_id=choice.request_id,
        value="Product Revenue Rule",
    )
    assert selected["input_request"] is not None
    assert selected["input_request"].artifact_name == "Product Revenue Rule"


def test_forecast_seed_followup_keeps_task_and_lists_current_rules(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    access = AccessControlService(settings.workflow_database_file)
    account = access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    repository = SQLiteAgentRepository(settings.workflow_database_file)
    service = AgentApplicationService(
        settings,
        gateway=AgentCapabilityGateway(
            settings,
            control_center=_ControlCenter(),
            data_review=_DataReview(),
            operation_catalog=_NamedRuleCatalog(
                "Actual to Forecast", "Plan to Forecast", "Aggregate Forecast"
            ),
        ),
        repository=repository,
        provider_factory=_NeverCalledProvider,
    )
    user = replace(_user(Permission.OPERATION_EXECUTE), user_id=account.user_id)
    conversation = service.create_conversation(user)
    repository.add_message(
        conversation_id=conversation.conversation_id,
        user_id=user.user_id,
        role=AgentMessageRole.USER,
        content="run forecast seeding",
    )
    repository.add_message(
        conversation_id=conversation.conversation_id,
        user_id=user.user_id,
        role=AgentMessageRole.ASSISTANT,
        content="Which Business Rule would you like?",
    )

    result = service.send_message(
        conversation_id=conversation.conversation_id,
        user=user,
        content="another rule",
    )

    choice = result["clarification_request"]
    assert choice is not None
    assert choice.operation_code == "business-rules"
    assert set(choice.options) == {
        "Actual to Forecast", "Plan to Forecast", "Aggregate Forecast"
    }
    assert result["approval_request"] is None


def test_business_rule_confirmation_retains_original_rule_request(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    access = AccessControlService(settings.workflow_database_file)
    account = access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    repository = SQLiteAgentRepository(settings.workflow_database_file)
    service = AgentApplicationService(
        settings,
        gateway=AgentCapabilityGateway(
            settings,
            control_center=_ControlCenter(),
            data_review=_DataReview(),
            operation_catalog=_NamedRuleCatalog("Aggregate Plan"),
        ),
        repository=repository,
        provider_factory=_NeverCalledProvider,
        operation_manager=_OperationManager(),
    )
    user = replace(
        _user(Permission.OPERATION_EXECUTE),
        user_id=account.user_id,
    )
    conversation = service.create_conversation(user)
    for role, content in (
        (AgentMessageRole.USER, "run aggregate plan rule"),
        (
            AgentMessageRole.ASSISTANT,
            "Would you like me to prepare the Aggregate Plan rule?",
        ),
        (AgentMessageRole.USER, "yes"),
        (
            AgentMessageRole.ASSISTANT,
            "Would you like me to prepare it now?",
        ),
    ):
        repository.add_message(
            conversation_id=conversation.conversation_id,
            user_id=user.user_id,
            role=role,
            content=content,
        )

    prepared = service.send_message(
        conversation_id=conversation.conversation_id,
        user=user,
        content="yes prepare now",
    )

    assert prepared["input_request"] is not None
    assert prepared["input_request"].artifact_name == "Aggregate Plan"


def test_standalone_flow_runs_after_one_complete_approval(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    access = AccessControlService(settings.workflow_database_file)
    access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    repository = SQLiteAgentRepository(settings.workflow_database_file)
    gateway = AgentCapabilityGateway(
        settings,
        control_center=_ControlCenter(),
        data_review=_DataReview(),
        operation_catalog=_StandaloneFlowCatalog(),
    )
    manager = _OperationManager()
    service = AgentApplicationService(
        settings,
        gateway=gateway,
        repository=repository,
        provider_factory=_NeverCalledProvider,
        operation_manager=manager,
    )
    user = replace(
        _user(Permission.OPERATION_EXECUTE),
        user_id=access.list_users()[0].user_id,
    )
    conversation = service.create_conversation(user)

    first = service.send_message(
        conversation_id=conversation.conversation_id,
        user=user,
        content=(
            "Configure and execute a standalone flow without an Oracle "
            "Pipeline for these operations: Business Rules -> Data Maps."
        ),
    )
    second = service.resolve_input(
        conversation_id=conversation.conversation_id,
        user=user,
        request_id=first["input_request"].request_id,
        values={
            "runtime_prompt_mode": "Use Calculation Manager defaults",
            "runtime_prompts": {},
        },
    )
    review = service.resolve_input(
        conversation_id=conversation.conversation_id,
        user=user,
        request_id=second["input_request"].request_id,
        values={
            "clear_target": False,
            "member_overrides": {},
            "exclusion_overrides": {},
        },
    )

    approval = review["approval_request"]
    assert approval.operation_code == "standalone-flow"
    assert [step["artifact_name"] for step in approval.input_values["steps"]] == [
        "Revenue Forecast",
        "Revenue to Reporting",
    ]
    completed = service.resolve_approval(
        conversation_id=conversation.conversation_id,
        user=user,
        request_id=approval.request_id,
        decision="approve",
    )

    assert completed["execution"] == {
        "execution_id": "flow-execution-1",
        "operation_code": "standalone-flow",
        "target_name": "Standalone Planning Flow",
        "status": "QUEUED",
    }
    flow_input, actor = manager.submissions[0]
    assert [step.artifact_name for step in flow_input.steps] == [
        "Revenue Forecast",
        "Revenue to Reporting",
    ]
    assert actor.trigger_source is TriggerSource.AI_AGENT


def test_standalone_flow_preserves_pipeline_and_following_rule(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    access = AccessControlService(settings.workflow_database_file)
    access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    repository = SQLiteAgentRepository(settings.workflow_database_file)
    gateway = AgentCapabilityGateway(
        settings,
        control_center=_ControlCenter(),
        data_review=_DataReview(),
        operation_catalog=_PipelineAndRuleCatalog(),
    )
    manager = _OperationManager()
    service = AgentApplicationService(
        settings,
        gateway=gateway,
        repository=repository,
        provider_factory=_NeverCalledProvider,
        operation_manager=manager,
    )
    user = replace(
        _user(Permission.OPERATION_EXECUTE),
        user_id=access.list_users()[0].user_id,
    )
    conversation = service.create_conversation(user)

    pipeline = service.send_message(
        conversation_id=conversation.conversation_id,
        user=user,
        content=(
            "Configure and execute a standalone flow without an Oracle "
            "Pipeline for these operations: Pipelines -> Business Rules. "
            "Objective: Run PIPE01, then Revenue Forecast."
        ),
    )
    assert pipeline["input_request"].operation_code == "pipelines"
    rule = service.resolve_input(
        conversation_id=conversation.conversation_id,
        user=user,
        request_id=pipeline["input_request"].request_id,
        values={
            "runtime_variables": {"YEAR": "FY27"},
            "file_choices": {
                "DataLoad_File": {
                    "source": "upload",
                    "upload_token": "forecast-upload-token",
                    "filename": "Forecast.csv",
                }
            },
        },
    )
    assert rule["input_request"].operation_code == "business-rules"
    review = service.resolve_input(
        conversation_id=conversation.conversation_id,
        user=user,
        request_id=rule["input_request"].request_id,
        values={
            "runtime_prompt_mode": "Use Calculation Manager defaults",
            "runtime_prompts": {},
        },
    )

    approval = review["approval_request"]
    assert [
        (step["operation_code"], step["artifact_name"])
        for step in approval.input_values["steps"]
    ] == [
        ("pipelines", "PIPE01"),
        ("business-rules", "Revenue Forecast"),
    ]
    upload_path = tmp_path / "Forecast.csv"
    upload_path.write_text("Account,Jan\nRevenue,100\n", encoding="utf-8")
    completed = service.resolve_approval(
        conversation_id=conversation.conversation_id,
        user=user,
        request_id=approval.request_id,
        decision="approve",
        operation_uploads={"step_1:DataLoad_File": upload_path},
    )

    assert completed["execution"]["operation_code"] == "standalone-flow"
    flow_input, _actor = manager.submissions[0]
    assert flow_input.steps[0].operation_input.pipeline_code == "PIPE01"
    assert flow_input.steps[0].operation_input.uploads == {
        "DataLoad_File": upload_path
    }
    assert flow_input.steps[1].operation_input.rule_name == "Revenue Forecast"


def test_pipeline_request_runs_full_governed_flow_without_model_tool_choice(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    access = AccessControlService(settings.workflow_database_file)
    access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    repository = SQLiteAgentRepository(settings.workflow_database_file)
    gateway = AgentCapabilityGateway(
        settings,
        control_center=_ControlCenter(),
        data_review=_DataReview(),
        operation_catalog=_PipelineCatalog(),
    )
    manager = _OperationManager()
    service = AgentApplicationService(
        settings,
        gateway=gateway,
        repository=repository,
        provider_factory=_NeverCalledProvider,
        operation_manager=manager,
    )
    user = replace(
        _user(Permission.OPERATION_EXECUTE),
        user_id=access.list_users()[0].user_id,
    )
    conversation = service.create_conversation(user)

    prepared = service.send_message(
        conversation_id=conversation.conversation_id,
        user=user,
        content="Run pipeline PIPE01.",
    )
    input_request = prepared["input_request"]
    assert input_request is not None
    assert input_request.artifact_name == "PIPE01"
    assert input_request.context["stages"][0]["display_name"] == (
        "Load forecast"
    )

    reviewed = service.resolve_input(
        conversation_id=conversation.conversation_id,
        user=user,
        request_id=input_request.request_id,
        values={
            "runtime_variables": {"YEAR": "FY27"},
            "file_choices": {
                "DataLoad_File": {
                    "source": "upload",
                    "upload_token": "forecast-upload-token",
                    "filename": "Forecast.csv",
                }
            },
        },
    )
    approval = reviewed["approval_request"]
    assert approval is not None
    assert approval.input_values["runtime_variables"] == {"YEAR": "FY27"}
    assert approval.input_values["uploads"] == {
        "DataLoad_File": "forecast-upload-token"
    }

    upload_path = tmp_path / "Forecast.csv"
    upload_path.write_text(
        "Account,Jan\nRevenue,100\n",
        encoding="utf-8",
    )
    cleanup = lambda: None
    completed = service.resolve_approval(
        conversation_id=conversation.conversation_id,
        user=user,
        request_id=approval.request_id,
        decision="approve",
        operation_uploads={"DataLoad_File": upload_path},
        operation_cleanup=cleanup,
    )

    assert completed["execution"]["operation_code"] == "pipelines"
    assert completed["execution"]["target_name"] == "PIPE01"
    operation_input, actor = manager.submissions[0]
    assert operation_input.pipeline_code == "PIPE01"
    assert operation_input.variables == {"YEAR": "FY27"}
    assert operation_input.uploads == {"DataLoad_File": upload_path}
    assert manager.cleanups == [cleanup]
    assert actor.trigger_source is TriggerSource.AI_AGENT
    decision = completed["decision"]
    assert decision.outcome_status == "SUBMITTED"
    assert decision.payload_snapshot["input_values"]["uploads"] == {
        "DataLoad_File": "[redacted]"
    }
    assert decision.payload_snapshot["input_values"]["upload_names"] == {
        "DataLoad_File": "Forecast.csv"
    }


def test_agent_rejection_decision_is_retained_without_execution(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    access = AccessControlService(settings.workflow_database_file)
    account = access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    repository = SQLiteAgentRepository(settings.workflow_database_file)
    conversation = repository.create_conversation(
        user_id=account.user_id,
        provider="gemini",
        model="test-model",
    )

    reserved, created = repository.reserve_action_decision(
        request_id="reject-1",
        conversation_id=conversation.conversation_id,
        user_id=account.user_id,
        username=account.username,
        operation_code="cube-refresh",
        artifact_name="Refresh_Cube",
        decision="REJECT",
        payload_checksum="a" * 64,
        payload_snapshot={"artifact_name": "Refresh_Cube"},
    )
    finalized = repository.finalize_action_decision(
        request_id="reject-1",
        user_id=account.user_id,
        conversation_id=conversation.conversation_id,
        outcome_status="REJECTED",
    )

    assert created is True
    assert reserved.outcome_status == "PROCESSING"
    assert finalized.decision == "REJECT"
    assert finalized.outcome_status == "REJECTED"
    assert finalized.execution_id is None


def test_data_integration_request_runs_governed_flow_without_model_tool_choice(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    access = AccessControlService(settings.workflow_database_file)
    access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    repository = SQLiteAgentRepository(settings.workflow_database_file)
    gateway = AgentCapabilityGateway(
        settings,
        control_center=_ControlCenter(),
        data_review=_DataReview(),
        operation_catalog=_DataIntegrationCatalog(),
    )
    manager = _OperationManager()
    service = AgentApplicationService(
        settings,
        gateway=gateway,
        repository=repository,
        provider_factory=_NeverCalledProvider,
        operation_manager=manager,
    )
    user = replace(
        _user(Permission.OPERATION_EXECUTE),
        user_id=access.list_users()[0].user_id,
    )
    conversation = service.create_conversation(user)

    prepared = service.send_message(
        conversation_id=conversation.conversation_id,
        user=user,
        content="Run Data Integration Revenue_Load.",
    )
    input_request = prepared["input_request"]
    assert input_request is not None
    assert input_request.artifact_name == "Revenue_Load"

    reviewed = service.resolve_input(
        conversation_id=conversation.conversation_id,
        user=user,
        request_id=input_request.request_id,
        values={
            "start_period": "Jan-27",
            "end_period": "Mar-27",
            "import_mode": "Replace",
            "export_mode": "Merge",
            "file_choice": {
                "source": "upload",
                "upload_token": "integration-upload-token",
                "filename": "Revenue.csv",
            },
        },
    )
    approval = reviewed["approval_request"]
    assert approval is not None
    assert approval.input_values["start_period"] == "Jan-27"
    assert approval.input_values["end_period"] == "Mar-27"
    assert approval.input_values["upload_token"] == (
        "integration-upload-token"
    )

    upload_path = tmp_path / "Revenue.csv"
    upload_path.write_text(
        "Account,Jan\nRevenue,100\n",
        encoding="utf-8",
    )
    cleanup = lambda: None
    completed = service.resolve_approval(
        conversation_id=conversation.conversation_id,
        user=user,
        request_id=approval.request_id,
        decision="approve",
        operation_uploads={"source_file": upload_path},
        operation_cleanup=cleanup,
    )

    assert completed["execution"]["operation_code"] == "data-integrations"
    assert completed["execution"]["target_name"] == "Revenue_Load"
    operation_input, actor = manager.submissions[0]
    assert operation_input.integration_name == "Revenue_Load"
    assert operation_input.start_period == "Jan-27"
    assert operation_input.end_period == "Mar-27"
    assert operation_input.import_mode == "Replace"
    assert operation_input.export_mode == "Merge"
    assert operation_input.upload_path == upload_path
    assert manager.cleanups == [cleanup]
    assert actor.trigger_source is TriggerSource.AI_AGENT


def test_data_map_request_runs_governed_flow_without_model_tool_choice(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    access = AccessControlService(settings.workflow_database_file)
    access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    repository = SQLiteAgentRepository(settings.workflow_database_file)
    gateway = AgentCapabilityGateway(
        settings,
        control_center=_ControlCenter(),
        data_review=_DataReview(),
        operation_catalog=_DataMapCatalog(),
    )
    manager = _OperationManager()
    service = AgentApplicationService(
        settings,
        gateway=gateway,
        repository=repository,
        provider_factory=_NeverCalledProvider,
        operation_manager=manager,
    )
    user = replace(
        _user(Permission.OPERATION_EXECUTE),
        user_id=access.list_users()[0].user_id,
    )
    conversation = service.create_conversation(user)

    prepared = service.send_message(
        conversation_id=conversation.conversation_id,
        user=user,
        content=(
            "Push data using the Revenue to Reporting Data Map."
        ),
    )
    input_request = prepared["input_request"]
    assert input_request is not None
    assert input_request.artifact_name == "Revenue to Reporting"

    reviewed = service.resolve_input(
        conversation_id=conversation.conversation_id,
        user=user,
        request_id=input_request.request_id,
        values={
            "clear_target": False,
            "member_overrides": {"Year": "FY27"},
            "exclusion_overrides": {"Entity": "No Entity"},
        },
    )
    approval = reviewed["approval_request"]
    assert approval is not None
    assert approval.input_values == {
        "clear_target": False,
        "member_overrides": {"Year": "FY27"},
        "exclusion_overrides": {"Entity": "No Entity"},
    }

    completed = service.resolve_approval(
        conversation_id=conversation.conversation_id,
        user=user,
        request_id=approval.request_id,
        decision="approve",
    )

    assert completed["execution"]["operation_code"] == "data-maps"
    assert completed["execution"]["target_name"] == "Revenue to Reporting"
    operation_input, actor = manager.submissions[0]
    assert operation_input.data_map_name == "Revenue to Reporting"
    assert operation_input.clear_target is False
    assert operation_input.member_overrides == {"Year": "FY27"}
    assert operation_input.exclusion_overrides == {"Entity": "No Entity"}
    assert actor.trigger_source is TriggerSource.AI_AGENT


def test_metadata_import_request_runs_governed_flow_without_model_tool_choice(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    access = AccessControlService(settings.workflow_database_file)
    access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    repository = SQLiteAgentRepository(settings.workflow_database_file)
    gateway = AgentCapabilityGateway(
        settings,
        control_center=_ControlCenter(),
        data_review=_DataReview(),
        operation_catalog=_MetadataImportCatalog(),
    )
    manager = _OperationManager()
    service = AgentApplicationService(
        settings,
        gateway=gateway,
        repository=repository,
        provider_factory=_NeverCalledProvider,
        operation_manager=manager,
    )
    user = replace(
        _user(Permission.OPERATION_EXECUTE),
        user_id=access.list_users()[0].user_id,
    )
    conversation = service.create_conversation(user)

    prepared = service.send_message(
        conversation_id=conversation.conversation_id,
        user=user,
        content="Run Import Products metadata job.",
    )
    input_request = prepared["input_request"]
    assert input_request is not None
    assert input_request.artifact_name == "Import Products"
    assert input_request.context["refresh_jobs"] == ["Refresh_Cube"]

    reviewed = service.resolve_input(
        conversation_id=conversation.conversation_id,
        user=user,
        request_id=input_request.request_id,
        values={
            "file_choice": {
                "source": "upload",
                "upload_token": "metadata-upload-token",
                "filename": "Products.csv",
            },
            "error_file_name": "Metadata_Errors.csv",
            "refresh_after_import": True,
            "refresh_job_name": "Refresh_Cube",
        },
    )
    approval = reviewed["approval_request"]
    assert approval is not None
    assert approval.input_values["refresh_after_import"] is True
    assert approval.input_values["refresh_job_name"] == "Refresh_Cube"

    upload_path = tmp_path / "Products.csv"
    upload_path.write_text(
        "Product,Alias\nP100,Phone\n",
        encoding="utf-8",
    )
    cleanup = lambda: None
    completed = service.resolve_approval(
        conversation_id=conversation.conversation_id,
        user=user,
        request_id=approval.request_id,
        decision="approve",
        operation_uploads={"source_file": upload_path},
        operation_cleanup=cleanup,
    )

    assert completed["execution"]["operation_code"] == "metadata-import"
    assert completed["execution"]["target_name"] == "Import Products"
    operation_input, actor = manager.submissions[0]
    assert operation_input.job_name == "Import Products"
    assert operation_input.upload_path == upload_path
    assert operation_input.error_file_name == "Metadata_Errors.csv"
    assert operation_input.refresh_job_name == "Refresh_Cube"
    assert manager.cleanups == [cleanup]
    assert actor.trigger_source is TriggerSource.AI_AGENT


def test_explicit_agent_approval_queues_data_map_without_redundant_draft(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    access = AccessControlService(settings.workflow_database_file)
    access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    repository = SQLiteAgentRepository(settings.workflow_database_file)
    gateway = AgentCapabilityGateway(
        settings,
        control_center=_ControlCenter(),
        data_review=_DataReview(),
        operation_catalog=_DataMapCatalog(),
    )
    manager = _OperationManager()
    service = AgentApplicationService(
        settings,
        gateway=gateway,
        repository=repository,
        graph_orchestrator=_ApprovedDataMapGraph(),
        operation_manager=manager,
    )
    user = replace(
        _user(Permission.OPERATION_EXECUTE),
        user_id=access.list_users()[0].user_id,
    )
    conversation = service.create_conversation(user)

    result = service.resolve_approval(
        conversation_id=conversation.conversation_id,
        user=user,
        request_id="approval-map-1",
        decision="approve",
    )

    assert result["action_drafts"] == ()
    assert result["execution"] == {
        "execution_id": "operation-execution-1",
        "operation_code": "data-maps",
        "target_name": "Revenue to Reporting",
        "status": "QUEUED",
    }
    operation_input, actor = manager.submissions[0]
    assert operation_input.data_map_name == "Revenue to Reporting"
    assert operation_input.clear_target is False
    assert operation_input.member_overrides == {"Year": "FY27"}
    assert operation_input.exclusion_overrides == {"Entity": "No Entity"}
    assert actor.trigger_source is TriggerSource.AI_AGENT
    assert service.get_action_drafts(conversation.conversation_id, user) == ()


def test_explicit_agent_approval_queues_pipeline_with_resolved_upload(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    access = AccessControlService(settings.workflow_database_file)
    access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    repository = SQLiteAgentRepository(settings.workflow_database_file)
    gateway = AgentCapabilityGateway(
        settings,
        control_center=_ControlCenter(),
        data_review=_DataReview(),
        operation_catalog=_PipelineCatalog(),
    )
    manager = _OperationManager()
    cleanup = lambda: None
    service = AgentApplicationService(
        settings,
        gateway=gateway,
        repository=repository,
        graph_orchestrator=_ApprovedPipelineGraph(),
        operation_manager=manager,
    )
    user = replace(
        _user(Permission.OPERATION_EXECUTE),
        user_id=access.list_users()[0].user_id,
    )
    conversation = service.create_conversation(user)
    upload_path = tmp_path / "Forecast.csv"
    upload_path.write_text("Account,Jan\nRevenue,100\n", encoding="utf-8")

    result = service.resolve_approval(
        conversation_id=conversation.conversation_id,
        user=user,
        request_id="approval-pipeline-1",
        decision="approve",
        operation_uploads={"DataLoad_File": upload_path},
        operation_cleanup=cleanup,
    )

    assert result["action_drafts"] == ()
    assert result["execution"]["operation_code"] == "pipelines"
    operation_input, actor = manager.submissions[0]
    assert operation_input.pipeline_code == "PIPE01"
    assert operation_input.variables == {"YEAR": "FY27"}
    assert operation_input.uploads == {"DataLoad_File": upload_path}
    assert operation_input.inbox_files == {}
    assert manager.cleanups == [cleanup]
    assert actor.trigger_source is TriggerSource.AI_AGENT


def test_explicit_agent_approval_queues_data_integration_with_resolved_upload(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    access = AccessControlService(settings.workflow_database_file)
    access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    repository = SQLiteAgentRepository(settings.workflow_database_file)
    gateway = AgentCapabilityGateway(
        settings,
        control_center=_ControlCenter(),
        data_review=_DataReview(),
        operation_catalog=_DataIntegrationCatalog(),
    )
    manager = _OperationManager()
    cleanup = lambda: None
    service = AgentApplicationService(
        settings,
        gateway=gateway,
        repository=repository,
        graph_orchestrator=_ApprovedDataIntegrationGraph(),
        operation_manager=manager,
    )
    user = replace(
        _user(Permission.OPERATION_EXECUTE),
        user_id=access.list_users()[0].user_id,
    )
    conversation = service.create_conversation(user)
    upload_path = tmp_path / "Revenue.csv"
    upload_path.write_text("Account,Jan\nRevenue,100\n", encoding="utf-8")

    result = service.resolve_approval(
        conversation_id=conversation.conversation_id,
        user=user,
        request_id="approval-integration-1",
        decision="approve",
        operation_uploads={"source_file": upload_path},
        operation_cleanup=cleanup,
    )

    assert result["action_drafts"] == ()
    assert result["execution"]["operation_code"] == "data-integrations"
    operation_input, actor = manager.submissions[0]
    assert operation_input.integration_name == "Revenue_Load"
    assert operation_input.start_period == "Jan-27"
    assert operation_input.end_period == "Mar-27"
    assert operation_input.import_mode == "Replace"
    assert operation_input.export_mode == "Merge"
    assert operation_input.upload_path == upload_path
    assert operation_input.inbox_file is None
    assert operation_input.use_configured_file is False
    assert manager.cleanups == [cleanup]
    assert actor.trigger_source is TriggerSource.AI_AGENT


def test_data_import_guided_inputs_and_approval_queue_resolved_upload(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    access = AccessControlService(settings.workflow_database_file)
    access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    repository = SQLiteAgentRepository(settings.workflow_database_file)
    gateway = AgentCapabilityGateway(
        settings,
        control_center=_ControlCenter(),
        data_review=_DataReview(),
        operation_catalog=_DataImportCatalog(),
    )
    normalized = gateway.normalize_guided_inputs(
        "data-import",
        "Import Forecast Data",
        {
            "file_choice": {
                "source": "upload",
                "upload_token": "data-import-upload-1",
                "filename": "Forecast.csv",
            },
            "error_file_name": "Forecast_Errors.log",
        },
    )
    assert normalized == {
        "file_source": "Upload on governed screen",
        "inbox_file": "",
        "upload_token": "data-import-upload-1",
        "upload_name": "Forecast.csv",
        "error_file_name": "Forecast_Errors.log",
    }

    manager = _OperationManager()
    cleanup = lambda: None
    service = AgentApplicationService(
        settings,
        gateway=gateway,
        repository=repository,
        graph_orchestrator=_ApprovedDataImportGraph(),
        operation_manager=manager,
    )
    user = replace(
        _user(Permission.OPERATION_EXECUTE),
        user_id=access.list_users()[0].user_id,
    )
    conversation = service.create_conversation(user)
    upload_path = tmp_path / "Forecast.csv"
    upload_path.write_text("Account,Jan\nRevenue,100\n", encoding="utf-8")

    result = service.resolve_approval(
        conversation_id=conversation.conversation_id,
        user=user,
        request_id="approval-data-import-1",
        decision="approve",
        operation_uploads={"source_file": upload_path},
        operation_cleanup=cleanup,
    )

    assert result["execution"]["operation_code"] == "data-import"
    operation_input, actor = manager.submissions[0]
    assert operation_input.job_name == "Import Forecast Data"
    assert operation_input.upload_path == upload_path
    assert operation_input.inbox_file is None
    assert operation_input.use_configured_file is False
    assert operation_input.error_file_name == "Forecast_Errors.log"
    assert manager.cleanups == [cleanup]
    assert actor.trigger_source is TriggerSource.AI_AGENT


def test_metadata_import_approval_queues_upload_and_conditional_refresh(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    access = AccessControlService(settings.workflow_database_file)
    access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    repository = SQLiteAgentRepository(settings.workflow_database_file)
    gateway = AgentCapabilityGateway(
        settings,
        control_center=_ControlCenter(),
        data_review=_DataReview(),
        operation_catalog=_MetadataImportCatalog(),
    )
    manager = _OperationManager()
    cleanup = lambda: None
    service = AgentApplicationService(
        settings,
        gateway=gateway,
        repository=repository,
        graph_orchestrator=_ApprovedMetadataImportGraph(),
        operation_manager=manager,
    )
    user = replace(
        _user(Permission.OPERATION_EXECUTE),
        user_id=access.list_users()[0].user_id,
    )
    conversation = service.create_conversation(user)
    upload_path = tmp_path / "Products.csv"
    upload_path.write_text("Product,Alias\nP100,Phone\n", encoding="utf-8")

    result = service.resolve_approval(
        conversation_id=conversation.conversation_id,
        user=user,
        request_id="approval-metadata-import-1",
        decision="approve",
        operation_uploads={"source_file": upload_path},
        operation_cleanup=cleanup,
    )

    assert result["execution"]["operation_code"] == "metadata-import"
    operation_input, actor = manager.submissions[0]
    assert operation_input.job_name == "Import Products"
    assert operation_input.upload_path == upload_path
    assert operation_input.error_file_name == "Metadata_Errors.csv"
    assert operation_input.refresh_job_name == "Refresh_Cube"
    assert manager.cleanups == [cleanup]
    assert actor.trigger_source is TriggerSource.AI_AGENT


def test_cube_refresh_approval_queues_exact_live_saved_job(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    access = AccessControlService(settings.workflow_database_file)
    access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    repository = SQLiteAgentRepository(settings.workflow_database_file)
    gateway = AgentCapabilityGateway(
        settings,
        control_center=_ControlCenter(),
        data_review=_DataReview(),
        operation_catalog=_CubeRefreshCatalog(),
    )
    manager = _OperationManager()
    service = AgentApplicationService(
        settings,
        gateway=gateway,
        repository=repository,
        graph_orchestrator=_ApprovedCubeRefreshGraph(),
        operation_manager=manager,
    )
    user = replace(
        _user(Permission.OPERATION_EXECUTE),
        user_id=access.list_users()[0].user_id,
    )
    conversation = service.create_conversation(user)

    result = service.resolve_approval(
        conversation_id=conversation.conversation_id,
        user=user,
        request_id="approval-cube-refresh-1",
        decision="approve",
    )

    assert result["action_drafts"] == ()
    assert result["execution"]["operation_code"] == "cube-refresh"
    operation_input, actor = manager.submissions[0]
    assert operation_input.job_name == "Refresh_Cube"
    assert manager.cleanups == [None]
    assert actor.trigger_source is TriggerSource.AI_AGENT


def test_substitution_variable_approval_queues_protected_live_update(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    access = AccessControlService(settings.workflow_database_file)
    access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    repository = SQLiteAgentRepository(settings.workflow_database_file)
    gateway = AgentCapabilityGateway(
        settings,
        control_center=_ControlCenter(),
        data_review=_DataReview(),
        substitution_variables=_SubstitutionVariables(),
    )
    manager = _OperationManager()
    service = AgentApplicationService(
        settings,
        gateway=gateway,
        repository=repository,
        graph_orchestrator=_ApprovedSubstitutionVariableGraph(),
        operation_manager=manager,
    )
    user = replace(
        _user(Permission.OPERATION_EXECUTE, Permission.VARIABLE_UPDATE),
        user_id=access.list_users()[0].user_id,
    )
    conversation = service.create_conversation(user)

    result = service.resolve_approval(
        conversation_id=conversation.conversation_id,
        user=user,
        request_id="approval-variable-1",
        decision="approve",
    )

    assert result["action_drafts"] == ()
    assert result["execution"]["operation_code"] == "substitution-variables"
    operation_input, actor = manager.submissions[0]
    assert operation_input.action.value == "UPDATE"
    assert operation_input.scope == "ALL"
    assert operation_input.name == "CurYr"
    assert operation_input.value == "FY27"
    assert operation_input.expected_current_value == "FY26"
    assert actor.trigger_source is TriggerSource.AI_AGENT


def test_substitution_variable_approval_requires_variable_permission(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    access = AccessControlService(settings.workflow_database_file)
    access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    repository = SQLiteAgentRepository(settings.workflow_database_file)
    gateway = AgentCapabilityGateway(
        settings,
        control_center=_ControlCenter(),
        data_review=_DataReview(),
        substitution_variables=_SubstitutionVariables(),
    )
    manager = _OperationManager()
    service = AgentApplicationService(
        settings,
        gateway=gateway,
        repository=repository,
        graph_orchestrator=_ApprovedSubstitutionVariableGraph(),
        operation_manager=manager,
    )
    user = replace(
        _user(Permission.OPERATION_EXECUTE),
        user_id=access.list_users()[0].user_id,
    )
    conversation = service.create_conversation(user)

    with pytest.raises(AgentConversationError, match="permission"):
        service.resolve_approval(
            conversation_id=conversation.conversation_id,
            user=user,
            request_id="approval-variable-1",
            decision="approve",
        )

    assert manager.submissions == []


def test_agent_catalog_recovery_requires_permission_and_continues_registration(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    repository = SQLiteAgentRepository(settings.workflow_database_file)
    catalog = _AgentRecoveryCatalog()
    gateway = AgentCapabilityGateway(
        settings,
        control_center=_ControlCenter(),
        data_review=_DataReview(),
        operation_catalog=catalog,
    )
    graph = _AgentRecoveryGraph()
    service = AgentApplicationService(
        settings,
        gateway=gateway,
        repository=repository,
        graph_orchestrator=graph,
    )
    access = AccessControlService(settings.workflow_database_file)
    access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    user = replace(
        _user(Permission.OPERATION_EXECUTE, Permission.CATALOG_MANAGE),
        user_id=access.list_users()[0].user_id,
    )
    conversation = service.create_conversation(user)

    refreshed = service.synchronize_clarification_catalog(
        conversation_id=conversation.conversation_id,
        user=user,
        request_id="recovery-1",
    )

    assert refreshed.options == ("PIPE_NEW",)
    assert refreshed.recommendations[0]["name"] == "PIPE_NEW"
    result = service.register_clarification_artifact(
        conversation_id=conversation.conversation_id,
        user=user,
        request_id="recovery-1",
        identifier="PIPE_NEW",
    )
    assert graph.selected == "PIPE_NEW"
    assert result["input_request"].artifact_name == "PIPE_NEW"

    unauthorized = replace(
        user,
        permissions=frozenset(
            {Permission.AGENT_USE, Permission.OPERATION_EXECUTE}
        ),
    )
    with pytest.raises(AgentConversationError, match="Catalog Manager"):
        service.synchronize_clarification_catalog(
            conversation_id=conversation.conversation_id,
            user=unauthorized,
            request_id="recovery-1",
        )


def test_legacy_process_inputs_are_migrated_to_business_fields() -> None:
    draft = AgentActionDraft(
        draft_id="draft-1",
        conversation_id="conversation-1",
        message_id=1,
        action_type="process",
        target_code="MONTHLY_FORECAST",
        display_name="Monthly Forecast",
        category="Planning process",
        risk_level="Elevated",
        route="/app/control-panel?process_code=MONTHLY_FORECAST",
        objective="Run the forecast.",
        artifact_name="MONTHLY_FORECAST",
        required_inputs=("Technical values",),
        stages=("Load", "Calculate"),
        approval_required=True,
        status="PREPARED_NOT_EXECUTED",
        created_at=datetime.now(UTC),
        input_values={
            "planning_year": "FY23",
            "runtime_variables": {
                "STARTPERIOD": "Jan",
                "ENDPERIOD": "Feb",
            },
            "inbox_files": {"DataLoad_File": "Data.csv"},
        },
    )

    migrated = AgentApplicationService._with_input_schema(draft)

    assert migrated.input_values["planning_year"] == "FY23"
    assert migrated.input_values["start_period"] == "Jan"
    assert migrated.input_values["end_period"] == "Feb"
    assert migrated.input_values["inbox_files"] == {
        "DataLoad_File": "Data.csv"
    }
    assert [item["key"] for item in migrated.input_schema] == [
        "planning_year",
        "start_period",
        "end_period",
    ]


def test_action_draft_preflight_is_permissioned_and_persisted(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    access = AccessControlService(settings.workflow_database_file)
    access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    repository = SQLiteAgentRepository(settings.workflow_database_file)
    gateway = AgentCapabilityGateway(
        settings,
        control_center=_ControlCenter(),
        data_review=_DataReview(),
    )
    service = AgentApplicationService(
        settings,
        gateway=gateway,
        preflight=AgentActionPreflightService(
            control_center=_ControlCenter(),
            operation_catalog=_OperationCatalog(),
            report_workspace=_ReportWorkspace(),
        ),
        repository=repository,
        provider_factory=_DraftProvider,
    )
    user = replace(
        _user(),
        permissions=frozenset(
            {Permission.AGENT_USE, Permission.OPERATION_EXECUTE}
        ),
    )
    conversation = service.create_conversation(user)
    response = service.send_message(
        conversation_id=conversation.conversation_id,
        user=user,
        content="Prepare the revenue forecast rule.",
    )

    validated = service.preflight_action_draft(
        response["action_drafts"][0].draft_id,
        user,
    )
    reloaded = service.get_action_drafts(
        conversation.conversation_id,
        user,
    )[0]

    assert validated.preflight_status == "READY_FOR_GOVERNED_REVIEW"
    assert [item.status for item in validated.preflight_checks] == [
        "PASS",
        "PASS",
        "PASS",
    ]
    assert reloaded.preflight_status == validated.preflight_status
    assert reloaded.preflight_at is not None

    handoff = service.resolve_action_handoff(
        draft_id=validated.draft_id,
        user=user,
        expected_target_code="business-rules",
        request_path="/app/operations/business-rules",
    )
    assert handoff.artifact_name == "Revenue Forecast"

    modern_handoff = service.resolve_operation_handoff(
        draft_id=validated.draft_id,
        user=user,
        target_code="business-rules",
    )
    assert modern_handoff.artifact_name == "Revenue Forecast"
    assert modern_handoff.target_code == "business-rules"

    with pytest.raises(AgentConversationError):
        service.resolve_action_handoff(
            draft_id=validated.draft_id,
            user=user,
            expected_target_code="data-maps",
            request_path="/app/operations/data-maps",
        )

    with pytest.raises(AgentConversationError):
        service.resolve_operation_handoff(
            draft_id=validated.draft_id,
            user=user,
            target_code="data-maps",
        )

    blocked = service.preflight_action_draft(
        validated.draft_id,
        replace(user, permissions=frozenset({Permission.AGENT_USE})),
    )
    assert blocked.preflight_status == "BLOCKED"
    assert blocked.preflight_checks[0].code == "permission"


def test_agent_status_does_not_break_platform_without_api_key(tmp_path: Path) -> None:
    settings = _settings(tmp_path, api_key=None)
    access = AccessControlService(settings.workflow_database_file)
    access.bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    gateway = AgentCapabilityGateway(
        settings,
        control_center=_ControlCenter(),
        data_review=_DataReview(),
    )
    service = AgentApplicationService(settings, gateway=gateway)

    status = service.status()

    assert status["enabled"] is False
    assert status["provider"] == "gemini"
    assert "GEMINI_API_KEY" in str(status["message"])


def test_gemini_adapter_supports_current_json_schema_field() -> None:
    class CurrentFunctionDeclaration:
        model_fields = {
            "name": object(),
            "parameters_json_schema": object(),
        }

        def __init__(self, **values) -> None:
            self.values = values

    provider = GeminiAgentProvider(
        api_key="test-key",
        model="test-model",
        client=object(),
        types_module=SimpleNamespace(
            FunctionDeclaration=CurrentFunctionDeclaration
        ),
    )
    tool = AgentCapabilityGateway.definitions()[0]

    declaration = provider._function_declaration(tool)

    assert declaration.values["parameters_json_schema"] == (
        tool.parameters_schema
    )


def test_gemini_function_result_is_returned_as_a_user_turn() -> None:
    class Declaration:
        model_fields = {"name": object(), "parameters": object()}

        def __init__(self, **values) -> None:
            self.values = values

    class Content:
        def __init__(self, *, role, parts) -> None:
            self.role = role
            self.parts = parts

    class Part:
        def __init__(self, **values) -> None:
            self.values = values

        @staticmethod
        def from_text(*, text):
            return {"text": text}

    class Value:
        def __init__(self, **values) -> None:
            self.values = values

    fake_types = SimpleNamespace(
        FunctionDeclaration=Declaration,
        FunctionResponse=Value,
        Content=Content,
        Part=Part,
        GenerateContentConfig=Value,
        Tool=Value,
        AutomaticFunctionCallingConfig=Value,
    )
    function_call = SimpleNamespace(
        id="call-123",
        name="get_environment_summary",
        args={},
    )
    first_response = SimpleNamespace(
        function_calls=(function_call,),
        candidates=(
            SimpleNamespace(
                content=Content(role="model", parts=[{"call": True}])
            ),
        ),
        text=None,
    )
    second_response = SimpleNamespace(
        function_calls=(),
        candidates=(),
        text="The environment is available.",
    )

    class Models:
        def __init__(self) -> None:
            self.calls = []

        def generate_content(self, **values):
            self.calls.append(values)
            return first_response if len(self.calls) == 1 else second_response

    models = Models()
    provider = GeminiAgentProvider(
        api_key="test-key",
        model="test-model",
        client=SimpleNamespace(models=models),
        types_module=fake_types,
    )
    now = datetime.now(UTC)

    result = provider.respond(
        messages=(
            AgentMessage(
                message_id=1,
                conversation_id="conversation",
                role=AgentMessageRole.USER,
                content="Describe the environment.",
                created_at=now,
            ),
        ),
        system_instruction="Read only.",
        tools=(
            AgentToolDefinition(
                name="get_environment_summary",
                description="Describe the environment.",
                parameters_schema={"type": "object", "properties": {}},
            ),
        ),
        execute_tool=lambda _: {"application": "Vision"},
    )

    second_contents = models.calls[1]["contents"]
    assert second_contents[-1].role == "user"
    function_response = second_contents[-1].parts[0].values[
        "function_response"
    ]
    assert function_response.values["id"] == "call-123"
    assert result.text == "The environment is available."
    assert result.tool_activity[0].status == "SUCCESS"

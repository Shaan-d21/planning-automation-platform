"""Unit tests for LangGraph agent routing and safety boundaries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.agent.capabilities import AgentCapabilityGateway
from app.agent.checkpoints import AgentCheckpointStore
from app.agent.graph import AgentGraphOrchestrator, GRAPH_TOOL_NAMES
from app.agent.task_state import AgentTaskInterpreter
from app.agent.models import (
    AgentMessage,
    AgentMessageRole,
    AgentProviderTurn,
    AgentToolCall,
)
from app.application.operations import (
    PipelineFilePreview,
    PipelineOperationPreview,
    PipelineStagePreview,
    PipelineVariablePreview,
)
from app.application.reports import ReportCatalogItem
from app.application.data_review import DataReviewComparison, DataReviewGrid
from app.application.substitution_variables import SubstitutionVariableCatalog
from app.application.user_variables import UserVariableCatalog
from app.config.settings import Settings
from app.models.substitution_variable import SubstitutionVariable
from app.models.environment import DimensionInfo
from app.models.data_validation import (
    DataMismatch,
    DataValidationResult,
    FormGrid,
    FormGridRow,
)
from app.models.workflow import (
    WorkflowRun,
    WorkflowStatus,
    WorkflowStepResult,
    WorkflowStepStatus,
)
from app.models.user_variable import UserVariableDefinition, UserVariableValue
from app.utils.exceptions import AgentProviderError


class _ControlCenter:
    def snapshot(self, *, history_limit):
        return type("Snapshot", (), {"processes": (), "recent_runs": ()})()


class _ExecutionEvidenceControlCenter(_ControlCenter):
    def __init__(self) -> None:
        started = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)
        self.run = WorkflowRun(
            execution_id="evidence-run-1",
            workflow_name="Planning Data Import - Forecast",
            status=WorkflowStatus.FAILED,
            started_at=started,
            completed_at=started + timedelta(seconds=12),
            error_message="The data load failed.",
            steps=(
                WorkflowStepResult(
                    name="Run Planning data import",
                    sequence=1,
                    status=WorkflowStepStatus.FAILED,
                    error_message="Invalid Entity member.",
                    details={
                        "job_id": 81,
                        "record_statistics": {
                            "records_read": 25,
                            "records_processed": 23,
                            "records_rejected": 2,
                            "details": [],
                        },
                    },
                ),
            ),
        )

    def list_workflow_runs(self, *, limit):
        assert limit == 100
        return (self.run,)

    def get_workflow_run(self, execution_id):
        return self.run if execution_id == self.run.execution_id else None


class _DataReview:
    def list_cubes(self):
        return ()


class _DiscoverableDataReview(_DataReview):
    def list_dimensions(self, cube):
        assert cube == "Plan2"
        return (
            DimensionInfo(name="Account", dimension_type="Account"),
            DimensionInfo(name="Period", dimension_type="Period"),
        )


class _UndiscoverableDataReview(_DataReview):
    def list_dimensions(self, cube):
        assert cube == "Plan2"
        raise RuntimeError(
            "Oracle returned no discoverable dimensions for cube 'Plan2'."
        )


class _ExactSliceDataReview(_UndiscoverableDataReview):
    def __init__(self) -> None:
        self.selection = None

    def load_slice(self, selection):
        self.selection = selection
        return DataReviewGrid(
            cube=selection.cube,
            form_name="Plan2 data review",
            grid=FormGrid(
                row_dimensions=("Account",),
                column_dimensions=("Period",),
                columns=(("Jan",), ("Feb",), ("Mar",)),
                rows=(
                    FormGridRow(
                        headers=("Units",),
                        data=(100, 110, 120),
                    ),
                ),
                pov=tuple(selection.pov.items()),
            ),
            row_count=1,
            column_count=3,
            cell_count=3,
            missing_cell_count=0,
        )


class _ReportWorkspace:
    def catalog(self):
        return (
            ReportCatalogItem(
                name="revenue-forecast",
                title="Revenue Forecast",
                cube="Plan2",
                default_pov=(
                    ("Scenario", "Actual"),
                    ("Year", "FY24"),
                    ("Product", "BaseData"),
                ),
                rows=(("Account", ("Revenue",)),),
                columns=(("Period", ("Jan", "Feb")),),
            ),
        )


class _VarianceDataReview(_DataReview):
    def compare_slices(
        self,
        source,
        target,
        *,
        tolerance,
        max_mismatches,
        include_cells,
    ):
        assert source.pov["Scenario"] == "Actual"
        assert target.pov["Scenario"] == "Budget"
        assert source.pov["Year"] == target.pov["Year"] == "FY26"
        assert source.pov["Product"] == target.pov["Product"] == "Snacks"
        assert source.columns[0].members == target.columns[0].members == ("Sep",)
        assert tolerance == 500
        assert max_mismatches == 100
        assert include_cells is False
        return DataReviewComparison(
            source_cube="Plan2",
            target_cube="Plan2",
            result=DataValidationResult(
                source_form="Actual Sep",
                target_form="Budget Sep",
                compared_cells=3,
                matched_cells=2,
                mismatches=(
                    DataMismatch(
                        row_headers=("Revenue",),
                        column_headers=("Sep",),
                        source_value=2000,
                        target_value=1000,
                        difference=1000,
                    ),
                ),
                tolerance=500,
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


class _DuplicateSubstitutionVariables:
    def discover(self):
        return SubstitutionVariableCatalog(
            variables=(
                SubstitutionVariable("CurYr", "FY26", "ALL"),
                SubstitutionVariable("CurYr", "FY25", "Plan1"),
            ),
            plan_types=(),
            scopes=("ALL", "Plan1"),
        )


class _EmptyScopeSubstitutionVariables:
    def discover(self):
        return SubstitutionVariableCatalog(
            variables=(),
            plan_types=(),
            scopes=(),
        )


class _UserVariables:
    def discover(self, user_name: str):
        return UserVariableCatalog(
            user_name=user_name,
            definitions=(
                UserVariableDefinition(name="MyEntity", dimension="Entity"),
            ),
            values=(
                UserVariableValue(
                    user_name=user_name,
                    name="MyEntity",
                    dimension="Entity",
                    member="Sales West",
                ),
            ),
        )


class _OperationCatalog:
    def discover_job_names(self, *, job_type):
        assert job_type == "RULES"
        return ("Revenue Forecast", "Gross Margin")

    def discover_registered(self):
        return type("Catalog", (), {"pipelines": (), "data_integrations": ()})()


class _TravelExpenseRuleCatalog:
    def discover_job_names(self, *, job_type):
        assert job_type == "RULES"
        return (
            "Create Forecast",
            "Update Adjustments",
            "calc_travelexpense",
        )

    def discover_registered(self):
        return type("Catalog", (), {"pipelines": (), "data_integrations": ()})()


class _DataMapOperationCatalog:
    def discover_job_names(self, *, job_type):
        assert job_type == "PLAN_TYPE_MAP"
        return ("Revenue to Reporting", "Workforce to Reporting")

    def discover_registered(self):
        return type("Catalog", (), {"pipelines": (), "data_integrations": ()})()


class _StandaloneFlowOperationCatalog:
    def discover_job_names(self, *, job_type):
        return {
            "RULES": ("Calculate Forecast",),
            "PLAN_TYPE_MAP": ("Forecast to Reporting",),
        }.get(job_type, ())

    def discover_registered(self):
        return type("Catalog", (), {"pipelines": (), "data_integrations": ()})()


class _CompleteStandaloneFlowOperationCatalog:
    def discover_job_names(self, *, job_type):
        return {
            "RULES": ("Calculate Forecast",),
            "PLAN_TYPE_MAP": ("Forecast to Reporting",),
        }.get(job_type, ())

    def discover_registered(self):
        integration = type("Integration", (), {"name": "Revenue Load"})()
        return type(
            "Catalog",
            (),
            {"pipelines": (), "data_integrations": (integration,)},
        )()


class _ForecastSeedingOperationCatalog:
    def discover_job_names(self, *, job_type):
        if job_type == "RULES":
            return ("Seed Forecast",)
        return ()

    def discover_registered(self):
        pipeline = type(
            "Pipeline",
            (),
            {"code": "FCST_SEED", "name": "Forecast Seeding"},
        )()
        integration = type(
            "Integration",
            (),
            {"name": "Forecast Seed Load"},
        )()
        return type(
            "Catalog",
            (),
            {
                "pipelines": (pipeline,),
                "data_integrations": (integration,),
            },
        )()


class _ForecastSeedRuleCatalog(_ForecastSeedingOperationCatalog):
    def discover_job_names(self, *, job_type):
        if job_type == "RULES":
            return (
                "Actual to Forecast",
                "Plan to Forecast",
                "Create Forecast",
                "Aggregate Forecast",
            )
        return ()


class _RepeatedRuleFlowOperationCatalog:
    """Live catalog with renamed rules and one Data Integration."""

    def discover_job_names(self, *, job_type):
        return {
            "RULES": (
                "BR_Calculate_Revenue_v2",
                "BR_Aggregate_Forecast_v2",
            ),
            "IMPORT_DATA": ("Import Forecast Data",),
        }.get(job_type, ())

    def discover_registered(self):
        integration = type(
            "Integration",
            (),
            {"name": "Revenue_Load_v2"},
        )()
        return type(
            "Catalog",
            (),
            {"pipelines": (), "data_integrations": (integration,)},
        )()

    def require_data_integration(self, integration_name):
        if integration_name != "Revenue_Load_v2":
            raise ValueError("Unknown integration")
        return type(
            "Artifact",
            (),
            {"oracle_identifier": integration_name},
        )()


class _NormalizedDataMapCatalog:
    def discover_job_names(self, *, job_type):
        assert job_type == "PLAN_TYPE_MAP"
        return (
            "Product_Revenue_to_Reporting",
            "Workforce_to_Reporting",
        )

    def discover_registered(self):
        return type("Catalog", (), {"pipelines": (), "data_integrations": ()})()


class _CompensationDataMapCatalog:
    def discover_job_names(self, *, job_type):
        assert job_type == "PLAN_TYPE_MAP"
        return (
            "OWP_Compensation Data",
            "OWP_Compensation Data for Reporting",
        )

    def discover_registered(self):
        return type("Catalog", (), {"pipelines": (), "data_integrations": ()})()


class _DataPushRoutingCatalog:
    def discover_job_names(self, *, job_type):
        if job_type == "PLAN_TYPE_MAP":
            return ("Product_Revenue_to_Reporting",)
        if job_type == "IMPORT_DATA":
            return ("Revenue_Load_Job",)
        raise AssertionError(f"Unexpected job type: {job_type}")

    def discover_registered(self):
        return type("Catalog", (), {"pipelines": (), "data_integrations": ()})()


class _DataImportRecommendationCatalog:
    def __init__(self, jobs: tuple[str, ...]) -> None:
        self.jobs = jobs

    def discover_job_names(self, *, job_type):
        assert job_type == "IMPORT_DATA"
        return self.jobs

    def discover_registered(self):
        return type("Catalog", (), {"pipelines": (), "data_integrations": ()})()


class _MetadataImportOperationCatalog:
    def discover_job_names(self, *, job_type):
        if job_type == "IMPORT_METADATA":
            return ("Import Products",)
        if job_type == "CUBE_REFRESH":
            return ("Refresh_Cube",)
        raise AssertionError(f"Unexpected job type: {job_type}")

    def discover_registered(self):
        return type("Catalog", (), {"pipelines": (), "data_integrations": ()})()


class _CubeRefreshOperationCatalog:
    def discover_job_names(self, *, job_type):
        assert job_type == "CUBE_REFRESH"
        return ("Refresh_Cube", "RefreshDatabase")

    def discover_registered(self):
        return type("Catalog", (), {"pipelines": (), "data_integrations": ()})()


class _MetadataImportRecommendationCatalog:
    def __init__(self, jobs: tuple[str, ...]) -> None:
        self.jobs = jobs

    def discover_job_names(self, *, job_type):
        if job_type == "IMPORT_METADATA":
            return self.jobs
        if job_type == "CUBE_REFRESH":
            return ()
        raise AssertionError(f"Unexpected job type: {job_type}")

    def discover_registered(self):
        return type("Catalog", (), {"pipelines": (), "data_integrations": ()})()


class _PipelineOperationCatalog:
    def discover_registered(self):
        pipeline = type(
            "Pipeline",
            (),
            {"code": "PIPE01", "name": "Monthly Revenue Forecast"},
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
                PipelineVariablePreview(
                    name="STARTPERIOD",
                    display_name="Start Period",
                    default_value=None,
                    required=True,
                    editable=True,
                ),
            ),
            file_requirements=(
                PipelineFilePreview(
                    key="DataLoad_File",
                    display_name="Forecast data",
                    configured_reference="Forecast.csv",
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


class _DataIntegrationOperationCatalog:
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


class _RecoverablePipelineCatalog:
    def __init__(self) -> None:
        self.registered = False

    def discover_registered(self):
        pipelines = ()
        if self.registered:
            pipelines = (
                type(
                    "Pipeline",
                    (),
                    {"code": "PIPE_NEW", "name": "Monthly Revenue Load"},
                )(),
            )
        return type(
            "Catalog",
            (),
            {"pipelines": pipelines, "data_integrations": ()},
        )()

    def preflight_pipeline(self, pipeline_code):
        assert pipeline_code == "PIPE_NEW"
        return PipelineOperationPreview(
            code="PIPE_NEW",
            display_name="Monthly Revenue Load",
            variables=(),
            file_requirements=(),
            stages=(),
        )


class _RecoverableDataIntegrationCatalog:
    def __init__(self) -> None:
        self.registered = False

    def discover_registered(self):
        return type(
            "Catalog",
            (),
            {"pipelines": (), "data_integrations": ()},
        )()

    def require_data_integration(self, integration_name):
        assert integration_name == "Revenue_Load_New"
        if not self.registered:
            raise ValueError("Not registered")
        return type(
            "Artifact",
            (),
            {"oracle_identifier": "Revenue_Load_New"},
        )()


class _SingleReadPipelineCatalog:
    def __init__(self) -> None:
        self.catalog_reads = 0

    def discover_registered(self):
        self.catalog_reads += 1
        if self.catalog_reads > 1:
            raise AssertionError("Pipeline catalog must be read once per decision.")
        pipeline = type(
            "Pipeline",
            (),
            {"code": "PL02", "name": "Monthly_Pipeline"},
        )()
        return type(
            "Catalog",
            (),
            {"pipelines": (pipeline,), "data_integrations": ()},
        )()

    def preflight_pipeline(self, pipeline_code):
        assert pipeline_code == "PL02"
        return PipelineOperationPreview(
            code="PL02",
            display_name="Monthly_Pipeline",
            variables=(),
            file_requirements=(),
            stages=(),
        )


class _StepProvider:
    provider_name = "test"
    model_name = "test-model"

    def __init__(
        self,
        *,
        requested_tool: str = "list_platform_operations",
        arguments: dict | None = None,
    ):
        self.requested_tool = requested_tool
        self.arguments = arguments or {}
        self.tool_names: tuple[str, ...] = ()
        self.system_instruction = ""

    def generate(
        self,
        *,
        messages,
        system_instruction,
        tools,
        provider_exchange,
    ):
        self.tool_names = tuple(item.name for item in tools)
        self.system_instruction = system_instruction
        if not provider_exchange:
            return AgentProviderTurn(
                tool_calls=(
                    AgentToolCall(
                        name=self.requested_tool,
                        arguments=self.arguments,
                        call_id="call-1",
                    ),
                ),
                provider_content={
                    "role": "model",
                    "parts": [{"function_call": self.requested_tool}],
                },
            )
        return AgentProviderTurn(text="The governed result is ready for review.")

    def tool_response(self, *, calls, activities):
        return {
            "role": "user",
            "parts": [
                {
                    "function_response": {
                        "name": call.name,
                        "status": activity.status,
                    }
                }
                for call, activity in zip(calls, activities, strict=True)
            ],
        }


class _NeverCalledProvider:
    provider_name = "never"
    model_name = "never"

    def generate(self, **_):
        raise AssertionError(
            "A direct execution-evidence request must not depend on model "
            "tool choice."
        )


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        epm_base_url="https://example.oraclecloud.com",
        epm_username="service-user",
        epm_password="secret",
        application_name="Vision",
        workflow_database_file=tmp_path / "workflow.sqlite3",
        gemini_api_key="test-key",
    )


def _message(content: str = "What operations can I run?") -> AgentMessage:
    return AgentMessage(
        message_id=1,
        conversation_id="conversation-1",
        role=AgentMessageRole.USER,
        content=content,
        created_at=datetime.now(UTC),
    )


def _orchestrator(
    tmp_path: Path,
    provider: _StepProvider,
    *,
    operation_catalog=None,
    substitution_variables=None,
    user_variables=None,
    control_center=None,
    data_review=None,
    report_workspace=None,
):
    settings = _settings(tmp_path)
    gateway = AgentCapabilityGateway(
        settings,
        control_center=control_center or _ControlCenter(),
        data_review=data_review or _DataReview(),
        report_workspace=report_workspace,
        operation_catalog=operation_catalog,
        substitution_variables=substitution_variables,
        user_variables=user_variables,
    )
    store = AgentCheckpointStore(settings.database_target)
    return AgentGraphOrchestrator(
        provider_factory=lambda: provider,
        gateway=gateway,
        checkpointer=store,
        system_instruction="Read only.",
        max_tool_rounds=2,
        environment_key="example|Vision",
    )


def test_graph_routes_model_tool_and_final_response(tmp_path: Path) -> None:
    provider = _StepProvider()
    graph = _orchestrator(tmp_path, provider)

    result = graph.invoke(
        conversation_id="conversation-1",
        user_id=7,
        messages=(_message(),),
    )

    assert result.text == "The governed result is ready for review."
    assert result.tool_activity[0].name == "list_platform_operations"
    assert result.tool_activity[0].status == "SUCCESS"
    assert set(provider.tool_names) == GRAPH_TOOL_NAMES
    assert "prepare_process_action" not in provider.tool_names
    assert "list_configured_processes" not in provider.tool_names


def test_graph_routes_selected_cube_directly_to_dimension_discovery(
    tmp_path: Path,
) -> None:
    graph = _orchestrator(
        tmp_path,
        _NeverCalledProvider(),
        data_review=_DiscoverableDataReview(),
    )

    result = graph.invoke(
        conversation_id="conversation-data-review-cube",
        user_id=7,
        messages=(
            _message(
                "Use cube Plan2 for this data review and help me choose the "
                "remaining POV, rows, and columns."
            ),
        ),
        allowed_tool_names=("list_cube_dimensions",),
    )

    assert result.tool_activity[0].name == "list_cube_dimensions"
    assert result.tool_activity[0].arguments == {"cube": "Plan2"}
    assert result.tool_activity[0].status == "SUCCESS"
    assert "live dimensions for **Plan2**" in result.text


def test_graph_keeps_selected_cube_when_dimension_discovery_is_unavailable(
    tmp_path: Path,
) -> None:
    graph = _orchestrator(
        tmp_path,
        _NeverCalledProvider(),
        data_review=_UndiscoverableDataReview(),
    )

    result = graph.invoke(
        conversation_id="conversation-data-review-compatibility",
        user_id=7,
        messages=(
            _message(
                "Use cube Plan2 for this data review and help me choose the "
                "remaining POV, rows, and columns."
            ),
        ),
        allowed_tool_names=("list_cube_dimensions",),
    )

    assert result.tool_activity[0].name == "list_cube_dimensions"
    assert result.tool_activity[0].status == "FAILED"
    assert "**Plan2 remains selected.**" in result.text
    assert "choose the cube again" in result.text


def test_graph_executes_exact_data_review_layout_without_model_interpretation(
    tmp_path: Path,
) -> None:
    data_review = _ExactSliceDataReview()
    graph = _orchestrator(
        tmp_path,
        _NeverCalledProvider(),
        data_review=data_review,
    )
    prompt = """Run an exact Data Review using this validated layout.
Cube: Plan2
POV:
Scenario=Actual
Version=Working
Entity=No Entity
Year=FY24
Rows:
Account=Units|Average_Selling_Price|Total_Revenue
Columns:
Period=Jan|Feb|Mar"""

    result = graph.invoke(
        conversation_id="conversation-data-review-exact-slice",
        user_id=7,
        messages=(_message(prompt),),
        allowed_tool_names=("review_data_slice",),
    )

    assert result.tool_activity[0].name == "review_data_slice"
    assert result.tool_activity[0].status == "SUCCESS"
    assert result.tool_activity[0].arguments == {
        "cube": "Plan2",
        "pov": [
            {"dimension": "Scenario", "member": "Actual"},
            {"dimension": "Version", "member": "Working"},
            {"dimension": "Entity", "member": "No Entity"},
            {"dimension": "Year", "member": "FY24"},
        ],
        "rows": [
            {
                "dimension": "Account",
                "members": [
                    "Units",
                    "Average_Selling_Price",
                    "Total_Revenue",
                ],
            }
        ],
        "columns": [
            {
                "dimension": "Period",
                "members": ["Jan", "Feb", "Mar"],
            }
        ],
    }
    assert data_review.selection is not None
    assert "live Planning slice" in result.text


def test_graph_opens_saved_data_explorer_view_without_model_interpretation(
    tmp_path: Path,
) -> None:
    data_review = _ExactSliceDataReview()
    graph = _orchestrator(
        tmp_path,
        _NeverCalledProvider(),
        data_review=data_review,
        report_workspace=_ReportWorkspace(),
    )

    result = graph.invoke(
        conversation_id="conversation-saved-data-view",
        user_id=7,
        messages=(
            _message(
                "Use saved Data Explorer view revenue-forecast and load its "
                "current Oracle data."
            ),
        ),
        allowed_tool_names=("review_saved_data_view",),
    )

    assert result.tool_activity[0].name == "review_saved_data_view"
    assert result.tool_activity[0].status == "SUCCESS"
    assert result.tool_activity[0].arguments == {"name": "revenue-forecast"}
    assert data_review.selection is not None
    assert data_review.selection.cube == "Plan2"
    assert "current Oracle values" in result.text


def test_graph_includes_only_validated_data_review_selection_context(
    tmp_path: Path,
) -> None:
    provider = _StepProvider()
    graph = _orchestrator(tmp_path, provider)

    graph.invoke(
        conversation_id="conversation-review-context",
        user_id=7,
        messages=(_message("Change FY27 to FY28."),),
        allowed_tool_names=("list_platform_operations",),
        data_review_context={
            "tool": "review_data_slice",
            "selection": {
                "cube": "Plan1",
                "pov": [{"dimension": "Year", "member": "FY27"}],
            },
        },
    )

    assert "Current tool-validated Data Explorer context" in (
        provider.system_instruction
    )
    assert '"cube":"Plan1"' in provider.system_instruction
    assert "financial values" in provider.system_instruction


def test_graph_inspects_latest_record_counts_without_model_tool_choice(
    tmp_path: Path,
) -> None:
    graph = _orchestrator(
        tmp_path,
        _NeverCalledProvider(),
        control_center=_ExecutionEvidenceControlCenter(),
    )

    result = graph.invoke(
        conversation_id="conversation-execution-statistics",
        user_id=7,
        messages=(
            _message("How many records were read, processed, and rejected?"),
        ),
        allowed_tool_names=("get_execution_evidence",),
    )

    assert "**25 records read**" in result.text
    assert "**23 processed**" in result.text
    assert "**2 rejected**" in result.text
    assert "Planning Data Import - Forecast" in result.text
    assert result.tool_activity[0].arguments == {"selector": "latest"}


def test_graph_inspects_latest_failed_run_without_model_tool_choice(
    tmp_path: Path,
) -> None:
    graph = _orchestrator(
        tmp_path,
        _NeverCalledProvider(),
        control_center=_ExecutionEvidenceControlCenter(),
    )

    result = graph.invoke(
        conversation_id="conversation-latest-failure",
        user_id=7,
        messages=(_message("Why did the latest job fail?"),),
        allowed_tool_names=("get_execution_evidence",),
    )

    assert "Invalid Entity member" in result.text
    assert result.tool_activity[0].arguments == {
        "selector": "latest_failed"
    }


def test_graph_blocks_retired_process_tool_even_if_model_requests_it(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(requested_tool="prepare_process_action")
    graph = _orchestrator(tmp_path, provider)

    result = graph.invoke(
        conversation_id="conversation-1",
        user_id=7,
        messages=(_message(),),
    )

    assert result.tool_activity[0].status == "FAILED"
    assert "not permitted" in result.tool_activity[0].summary


def test_graph_exposes_only_tools_allowed_for_current_user(tmp_path: Path) -> None:
    provider = _StepProvider()
    graph = _orchestrator(tmp_path, provider)

    graph.invoke(
        conversation_id="conversation-allowed-tools",
        user_id=7,
        messages=(_message(),),
        allowed_tool_names=("list_platform_operations",),
    )

    assert provider.tool_names == ("list_platform_operations",)


def test_graph_pauses_before_preparing_governed_operation(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(
        requested_tool="prepare_operation_action",
        arguments={
            "operation_code": "business-rules",
            "objective": "Calculate the approved forecast.",
            "artifact_name": "Revenue Forecast",
        },
    )
    graph = _orchestrator(tmp_path, provider)

    paused = graph.invoke(
        conversation_id="conversation-1",
        user_id=7,
        messages=(_message(),),
    )

    assert paused.approval_request is not None
    assert paused.approval_request.operation_code == "business-rules"
    assert paused.tool_activity == ()
    assert graph.pending_approval(
        conversation_id="conversation-1",
        user_id=7,
    ) == paused.approval_request

    completed = graph.resume_approval(
        conversation_id="conversation-1",
        user_id=7,
        request_id=paused.approval_request.request_id,
        decision="approve",
    )

    assert completed.approval_request is None
    assert completed.tool_activity[0].status == "SUCCESS"
    assert completed.tool_activity[0].result["action_draft"]["status"] == (
        "PREPARED_NOT_EXECUTED"
    )
    assert graph.pending_approval(
        conversation_id="conversation-1",
        user_id=7,
    ) is None


def test_graph_rejection_cancels_operation_preparation(tmp_path: Path) -> None:
    provider = _StepProvider(
        requested_tool="prepare_operation_action",
        arguments={
            "operation_code": "data-maps",
            "objective": "Publish approved data.",
        },
    )
    graph = _orchestrator(tmp_path, provider)
    paused = graph.invoke(
        conversation_id="conversation-2",
        user_id=7,
        messages=(_message(),),
    )
    assert paused.approval_request is not None

    completed = graph.resume_approval(
        conversation_id="conversation-2",
        user_id=7,
        request_id=paused.approval_request.request_id,
        decision="reject",
    )

    assert completed.tool_activity[0].status == "CANCELLED"
    assert "rejected" in completed.tool_activity[0].summary


def test_graph_clarifies_missing_artifact_before_approval(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(
        requested_tool="prepare_operation_action",
        arguments={
            "operation_code": "business-rules",
            "objective": "Calculate the forecast.",
        },
    )
    graph = _orchestrator(
        tmp_path,
        provider,
        operation_catalog=_OperationCatalog(),
    )

    clarification_result = graph.invoke(
        conversation_id="conversation-clarification",
        user_id=7,
        messages=(_message(),),
    )

    clarification = clarification_result.clarification_request
    assert clarification is not None
    assert clarification.options == ("Revenue Forecast", "Gross Margin")
    assert clarification.recommendations[0]["name"] == "Revenue Forecast"
    assert clarification.recommendations[0]["confidence"] in {
        "Strong match",
        "Possible match",
    }
    assert clarification_result.approval_request is None

    input_result = graph.resume_clarification(
        conversation_id="conversation-clarification",
        user_id=7,
        request_id=clarification.request_id,
        value="gross margin",
    )

    input_request = input_result.input_request
    assert input_request is not None
    assert input_request.artifact_name == "Gross Margin"
    assert input_request.fields[0]["key"] == "runtime_prompt_mode"

    approval_result = graph.resume_input(
        conversation_id="conversation-clarification",
        user_id=7,
        request_id=input_request.request_id,
        values={
            "runtime_prompt_mode": "Use Calculation Manager defaults",
            "runtime_prompts": {},
        },
    )

    approval = approval_result.approval_request
    assert approval is not None
    assert approval.artifact_name == "Gross Margin"
    assert approval.input_values == {"runtime_prompts": {}}

    completed = graph.resume_approval(
        conversation_id="conversation-clarification",
        user_id=7,
        request_id=approval.request_id,
        decision="approve",
    )

    assert completed.tool_activity[0].result["action_draft"][
        "artifact_name"
    ] == "Gross Margin"
    assert completed.tool_activity[0].result["action_draft"][
        "input_values"
    ] == {"runtime_prompts": {}}


def test_graph_carries_confirmed_rule_from_previous_assistant_turn(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(
        requested_tool="prepare_operation_action",
        arguments={
            "operation_code": "business-rules",
            "objective": "Prepare the requested Business Rule.",
        },
    )
    graph = _orchestrator(
        tmp_path,
        provider,
        operation_catalog=_TravelExpenseRuleCatalog(),
    )
    created_at = datetime.now(UTC)

    result = graph.invoke(
        conversation_id="conversation-confirm-travel-rule",
        user_id=7,
        messages=(
            AgentMessage(
                message_id=1,
                conversation_id="conversation-confirm-travel-rule",
                role=AgentMessageRole.USER,
                content="Prepare a business rule for travel expense.",
                created_at=created_at,
            ),
            AgentMessage(
                message_id=2,
                conversation_id="conversation-confirm-travel-rule",
                role=AgentMessageRole.ASSISTANT,
                content=(
                    "The matching live rule is calc_travelexpense. "
                    "Would you like me to prepare it?"
                ),
                created_at=created_at,
            ),
            AgentMessage(
                message_id=3,
                conversation_id="conversation-confirm-travel-rule",
                role=AgentMessageRole.USER,
                content="yes prepare",
                created_at=created_at,
            ),
        ),
    )

    assert result.clarification_request is None
    assert result.input_request is not None
    assert result.input_request.operation_code == "business-rules"
    assert result.input_request.artifact_name == "calc_travelexpense"


def test_graph_enters_business_rule_workflow_without_provider_tool_choice(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(requested_tool="list_platform_operations")
    graph = _orchestrator(
        tmp_path,
        provider,
        operation_catalog=_TravelExpenseRuleCatalog(),
    )

    result = graph.invoke(
        conversation_id="conversation-direct-travel-rule",
        user_id=7,
        messages=(
            _message("Prepare a business rule for travel expense."),
        ),
    )

    clarification = result.clarification_request
    assert clarification is not None
    assert clarification.recommendations[0]["name"] == "calc_travelexpense"
    assert provider.tool_names == ()


def test_graph_resolves_exact_business_rule_without_provider_tool_choice(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(requested_tool="list_platform_operations")
    graph = _orchestrator(
        tmp_path,
        provider,
        operation_catalog=_TravelExpenseRuleCatalog(),
    )

    result = graph.invoke(
        conversation_id="conversation-exact-travel-rule",
        user_id=7,
        messages=(
            _message("Run business rule calc_travelexpense."),
        ),
    )

    assert result.clarification_request is None
    assert result.input_request is not None
    assert result.input_request.artifact_name == "calc_travelexpense"
    approval_result = graph.resume_input(
        conversation_id="conversation-exact-travel-rule",
        user_id=7,
        request_id=result.input_request.request_id,
        values={
            "runtime_prompt_mode": "Provide runtime prompt values",
            "runtime_prompts": {"Entity": "Travel Operations"},
        },
    )
    assert approval_result.approval_request is not None
    assert approval_result.approval_request.input_values == {
        "runtime_prompts": {"Entity": "Travel Operations"}
    }

    completed = graph.resume_approval(
        conversation_id="conversation-exact-travel-rule",
        user_id=7,
        request_id=approval_result.approval_request.request_id,
        decision="approve",
    )

    assert completed.tool_activity[0].status == "SUCCESS"
    assert completed.tool_activity[0].result["action_draft"][
        "artifact_name"
    ] == "calc_travelexpense"
    assert completed.tool_activity[0].result["action_draft"][
        "input_values"
    ] == {"runtime_prompts": {"Entity": "Travel Operations"}}
    assert provider.tool_names == ()


def test_business_rule_guidance_still_uses_the_model(tmp_path: Path) -> None:
    provider = _StepProvider(requested_tool="list_platform_operations")
    graph = _orchestrator(
        tmp_path,
        provider,
        operation_catalog=_TravelExpenseRuleCatalog(),
    )

    result = graph.invoke(
        conversation_id="conversation-rule-guidance",
        user_id=7,
        messages=(
            _message("How do business rules work?"),
        ),
    )

    assert result.text == "The governed result is ready for review."
    assert "list_platform_operations" in provider.tool_names


@pytest.mark.parametrize(
    ("reply", "expected"),
    (
        ("yes prepare", True),
        ("okay, go ahead", True),
        ("run it", True),
        ("prepare Gross Margin instead", False),
        ("what does this rule do", False),
    ),
)
def test_artifact_confirmation_reply_is_conservative(
    reply: str,
    expected: bool,
) -> None:
    assert (
        AgentGraphOrchestrator._is_artifact_confirmation_reply(reply)
        is expected
    )


def test_graph_collects_and_preserves_business_rule_runtime_prompts(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(
        requested_tool="prepare_operation_action",
        arguments={
            "operation_code": "business-rules",
            "objective": "Calculate the forecast.",
        },
    )
    graph = _orchestrator(
        tmp_path,
        provider,
        operation_catalog=_OperationCatalog(),
    )
    clarified = graph.invoke(
        conversation_id="conversation-rule-rtps",
        user_id=7,
        messages=(_message(),),
    )
    input_result = graph.resume_clarification(
        conversation_id="conversation-rule-rtps",
        user_id=7,
        request_id=clarified.clarification_request.request_id,
        value="Revenue Forecast",
    )

    approval_result = graph.resume_input(
        conversation_id="conversation-rule-rtps",
        user_id=7,
        request_id=input_result.input_request.request_id,
        values={
            "runtime_prompt_mode": "Provide runtime prompt values",
            "runtime_prompts": {"Entity": "Sales East", "Year": "FY27"},
        },
    )

    assert approval_result.approval_request.input_values == {
        "runtime_prompts": {"Entity": "Sales East", "Year": "FY27"}
    }
    completed = graph.resume_approval(
        conversation_id="conversation-rule-rtps",
        user_id=7,
        request_id=approval_result.approval_request.request_id,
        decision="approve",
    )
    assert completed.tool_activity[0].result["action_draft"][
        "input_values"
    ] == {"runtime_prompts": {"Entity": "Sales East", "Year": "FY27"}}


def test_graph_collects_data_map_controls_before_single_approval(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(
        requested_tool="prepare_operation_action",
        arguments={
            "operation_code": "data-maps",
            "objective": "Publish approved revenue to reporting.",
        },
    )
    graph = _orchestrator(
        tmp_path,
        provider,
        operation_catalog=_DataMapOperationCatalog(),
    )
    clarified = graph.invoke(
        conversation_id="conversation-data-map",
        user_id=7,
        messages=(_message(),),
    )

    input_result = graph.resume_clarification(
        conversation_id="conversation-data-map",
        user_id=7,
        request_id=clarified.clarification_request.request_id,
        value="Revenue to Reporting",
    )

    assert input_result.input_request is not None
    assert [item["key"] for item in input_result.input_request.fields] == [
        "clear_target",
        "member_overrides",
        "exclusion_overrides",
    ]
    approval_result = graph.resume_input(
        conversation_id="conversation-data-map",
        user_id=7,
        request_id=input_result.input_request.request_id,
        values={
            "clear_target": False,
            "member_overrides": {"Year": "FY27"},
            "exclusion_overrides": {},
        },
    )

    approval = approval_result.approval_request
    assert approval is not None
    assert approval.operation_code == "data-maps"
    assert approval.effect.startswith("Start the selected Data Map")
    completed = graph.resume_approval(
        conversation_id="conversation-data-map",
        user_id=7,
        request_id=approval.request_id,
        decision="approve",
    )
    assert completed.tool_activity[0].result["action_draft"][
        "input_values"
    ] == {
        "clear_target": False,
        "member_overrides": {"Year": "FY27"},
        "exclusion_overrides": {},
    }


def test_graph_collects_live_pipeline_inputs_before_single_approval(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(
        requested_tool="prepare_operation_action",
        arguments={
            "operation_code": "pipelines",
            "objective": "Run the monthly forecast lifecycle.",
            "artifact_name": "PIPE01",
        },
    )
    graph = _orchestrator(
        tmp_path,
        provider,
        operation_catalog=_PipelineOperationCatalog(),
    )

    paused = graph.invoke(
        conversation_id="conversation-pipeline-inputs",
        user_id=7,
        messages=(_message("Run Pipeline PIPE01 for FY27."),),
    )

    assert paused.input_request is not None
    assert paused.input_request.operation_code == "pipelines"
    assert paused.input_request.context["stages"][0]["display_name"] == (
        "Load forecast"
    )
    approval = graph.resume_input(
        conversation_id="conversation-pipeline-inputs",
        user_id=7,
        request_id=paused.input_request.request_id,
        values={
            "runtime_variables": {
                "YEAR": "FY27",
                "STARTPERIOD": "Jan-27",
            },
            "file_choices": {
                "DataLoad_File": {"source": "configured"},
            },
        },
    )

    assert approval.approval_request is not None
    assert approval.approval_request.effect.startswith(
        "Start the selected Oracle Pipeline"
    )
    assert approval.approval_request.input_values == {
        "runtime_variables": {
            "YEAR": "FY27",
            "STARTPERIOD": "Jan-27",
        },
        "uploads": {},
        "upload_names": {},
        "inbox_files": {},
        "configured_files": {"DataLoad_File": "Forecast.csv"},
    }


def test_graph_collects_data_integration_inputs_before_single_approval(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(
        requested_tool="prepare_operation_action",
        arguments={
            "operation_code": "data-integrations",
            "objective": "Load monthly revenue data.",
            "artifact_name": "Revenue_Load",
        },
    )
    graph = _orchestrator(
        tmp_path,
        provider,
        operation_catalog=_DataIntegrationOperationCatalog(),
    )

    paused = graph.invoke(
        conversation_id="conversation-data-integration-inputs",
        user_id=7,
        messages=(_message("Run Revenue Load Data Integration."),),
    )

    assert paused.input_request is not None
    assert paused.input_request.operation_code == "data-integrations"
    assert paused.input_request.artifact_name == "Revenue_Load"
    approval = graph.resume_input(
        conversation_id="conversation-data-integration-inputs",
        user_id=7,
        request_id=paused.input_request.request_id,
        values={
            "start_period": "Jan-27",
            "end_period": "Mar-27",
            "import_mode": "Replace",
            "export_mode": "Merge",
            "file_choice": {"source": "configured"},
        },
    )

    assert approval.approval_request is not None
    assert approval.approval_request.effect.startswith(
        "Start the selected Data Integration"
    )
    assert approval.approval_request.input_values == {
        "start_period": "Jan-27",
        "end_period": "Mar-27",
        "import_mode": "Replace",
        "export_mode": "Merge",
        "file_source": "Use file configured in Oracle",
        "inbox_file": "",
        "upload_token": "",
        "upload_name": "",
    }
    assert provider.tool_names == ()


def test_graph_carries_confirmed_data_integration_from_previous_turn(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(requested_tool="list_platform_operations")
    graph = _orchestrator(
        tmp_path,
        provider,
        operation_catalog=_DataIntegrationOperationCatalog(),
    )
    created_at = datetime.now(UTC)

    result = graph.invoke(
        conversation_id="conversation-confirm-integration",
        user_id=7,
        messages=(
            AgentMessage(
                message_id=1,
                conversation_id="conversation-confirm-integration",
                role=AgentMessageRole.USER,
                content="Which Data Integration loads monthly revenue?",
                created_at=created_at,
            ),
            AgentMessage(
                message_id=2,
                conversation_id="conversation-confirm-integration",
                role=AgentMessageRole.ASSISTANT,
                content=(
                    "The registered Data Integration is Revenue_Load. "
                    "Would you like me to prepare it?"
                ),
                created_at=created_at,
            ),
            AgentMessage(
                message_id=3,
                conversation_id="conversation-confirm-integration",
                role=AgentMessageRole.USER,
                content="yes prepare",
                created_at=created_at,
            ),
        ),
    )

    assert result.clarification_request is None
    assert result.input_request is not None
    assert result.input_request.operation_code == "data-integrations"
    assert result.input_request.artifact_name == "Revenue_Load"
    assert provider.tool_names == ()


def test_data_integration_guidance_still_uses_the_model(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(requested_tool="list_platform_operations")
    graph = _orchestrator(
        tmp_path,
        provider,
        operation_catalog=_DataIntegrationOperationCatalog(),
    )

    result = graph.invoke(
        conversation_id="conversation-integration-guidance",
        user_id=7,
        messages=(_message("How do Data Integrations work?"),),
    )

    assert result.text == "The governed result is ready for review."
    assert "list_platform_operations" in provider.tool_names


def test_graph_resolves_pipeline_by_business_facing_name(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(
        requested_tool="prepare_operation_action",
        arguments={
            "operation_code": "pipelines",
            "objective": "Run the selected lifecycle.",
        },
    )
    graph = _orchestrator(
        tmp_path,
        provider,
        operation_catalog=_PipelineOperationCatalog(),
    )

    result = graph.invoke(
        conversation_id="conversation-pipeline-display-name",
        user_id=7,
        messages=(
            _message("Run the Monthly Revenue Forecast Pipeline."),
        ),
    )

    assert result.clarification_request is None
    assert result.input_request is not None
    assert result.input_request.artifact_name == "PIPE01"
    assert provider.tool_names == ()


def test_graph_carries_confirmed_pipeline_from_previous_assistant_turn(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(requested_tool="list_platform_operations")
    graph = _orchestrator(
        tmp_path,
        provider,
        operation_catalog=_PipelineOperationCatalog(),
    )
    created_at = datetime.now(UTC)

    result = graph.invoke(
        conversation_id="conversation-confirm-pipeline",
        user_id=7,
        messages=(
            AgentMessage(
                message_id=1,
                conversation_id="conversation-confirm-pipeline",
                role=AgentMessageRole.USER,
                content="Which pipeline runs the monthly revenue forecast?",
                created_at=created_at,
            ),
            AgentMessage(
                message_id=2,
                conversation_id="conversation-confirm-pipeline",
                role=AgentMessageRole.ASSISTANT,
                content=(
                    "The matching live Pipeline is Monthly Revenue Forecast "
                    "(PIPE01). Would you like me to prepare it?"
                ),
                created_at=created_at,
            ),
            AgentMessage(
                message_id=3,
                conversation_id="conversation-confirm-pipeline",
                role=AgentMessageRole.USER,
                content="yes prepare",
                created_at=created_at,
            ),
        ),
    )

    assert result.clarification_request is None
    assert result.input_request is not None
    assert result.input_request.operation_code == "pipelines"
    assert result.input_request.artifact_name == "PIPE01"
    assert provider.tool_names == ()


def test_pipeline_guidance_still_uses_the_model(tmp_path: Path) -> None:
    provider = _StepProvider(requested_tool="list_platform_operations")
    graph = _orchestrator(
        tmp_path,
        provider,
        operation_catalog=_PipelineOperationCatalog(),
    )

    result = graph.invoke(
        conversation_id="conversation-pipeline-guidance",
        user_id=7,
        messages=(_message("How do Oracle Pipelines work?"),),
    )

    assert result.text == "The governed result is ready for review."
    assert "list_platform_operations" in provider.tool_names


def test_graph_resolves_pipeline_before_approval_from_one_catalog_snapshot(
    tmp_path: Path,
) -> None:
    catalog = _SingleReadPipelineCatalog()
    provider = _StepProvider(
        requested_tool="prepare_operation_action",
        arguments={
            "operation_code": "pipelines",
            "objective": "Process monthly data loads and transformations.",
            "artifact_name": "Monthly_Pipeline",
        },
    )
    graph = _orchestrator(
        tmp_path,
        provider,
        operation_catalog=catalog,
    )

    result = graph.invoke(
        conversation_id="conversation-monthly-pipeline",
        user_id=7,
        messages=(_message("Prepare me monthly pipeline"),),
    )

    assert result.approval_request is None
    assert result.clarification_request is None
    assert result.input_request is not None
    assert result.input_request.artifact_name == "PL02"
    assert catalog.catalog_reads == 1


def test_graph_recommends_pipeline_by_business_purpose(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(
        requested_tool="prepare_operation_action",
        arguments={
            "operation_code": "pipelines",
            "objective": "Prepare a revenue forecast.",
        },
    )
    graph = _orchestrator(
        tmp_path,
        provider,
        operation_catalog=_PipelineOperationCatalog(),
    )

    result = graph.invoke(
        conversation_id="conversation-pipeline-recommendation",
        user_id=7,
        messages=(_message("Run the revenue forecast process."),),
    )

    assert result.clarification_request is not None
    assert result.clarification_request.recommendations[0]["name"] == "PIPE01"
    assert result.clarification_request.recommendations[0][
        "display_name"
    ] == "Monthly Revenue Forecast"
    assert result.clarification_request.option_labels == {
        "PIPE01": "Monthly Revenue Forecast"
    }


def test_graph_auto_selects_exact_data_map_named_with_spaces(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(
        requested_tool="prepare_operation_action",
        arguments={
            "operation_code": "data-maps",
            "objective": "Prepare the requested Data Map.",
        },
    )
    graph = _orchestrator(
        tmp_path,
        provider,
        operation_catalog=_NormalizedDataMapCatalog(),
    )

    result = graph.invoke(
        conversation_id="conversation-auto-data-map",
        user_id=7,
        messages=(
            _message(
                "Prepare Product Revenue to Reporting data map for execution."
            ),
        ),
    )

    assert result.clarification_request is None
    assert result.input_request is not None
    assert result.input_request.artifact_name == (
        "Product_Revenue_to_Reporting"
    )
    assert provider.tool_names == ()


def test_graph_carries_confirmed_data_map_from_previous_assistant_turn(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(requested_tool="list_platform_operations")
    graph = _orchestrator(
        tmp_path,
        provider,
        operation_catalog=_NormalizedDataMapCatalog(),
    )
    created_at = datetime.now(UTC)

    result = graph.invoke(
        conversation_id="conversation-confirm-data-map",
        user_id=7,
        messages=(
            AgentMessage(
                message_id=1,
                conversation_id="conversation-confirm-data-map",
                role=AgentMessageRole.USER,
                content="Which Data Map publishes product revenue?",
                created_at=created_at,
            ),
            AgentMessage(
                message_id=2,
                conversation_id="conversation-confirm-data-map",
                role=AgentMessageRole.ASSISTANT,
                content=(
                    "The matching Data Map is "
                    "Product_Revenue_to_Reporting. Should I prepare it?"
                ),
                created_at=created_at,
            ),
            AgentMessage(
                message_id=3,
                conversation_id="conversation-confirm-data-map",
                role=AgentMessageRole.USER,
                content="yes prepare",
                created_at=created_at,
            ),
        ),
    )

    assert result.clarification_request is None
    assert result.input_request is not None
    assert result.input_request.operation_code == "data-maps"
    assert result.input_request.artifact_name == (
        "Product_Revenue_to_Reporting"
    )
    assert provider.tool_names == ()


def test_data_map_guidance_still_uses_the_model(tmp_path: Path) -> None:
    provider = _StepProvider(requested_tool="list_platform_operations")
    graph = _orchestrator(
        tmp_path,
        provider,
        operation_catalog=_NormalizedDataMapCatalog(),
    )

    result = graph.invoke(
        conversation_id="conversation-data-map-guidance",
        user_id=7,
        messages=(_message("How do Data Maps work?"),),
    )

    assert result.text == "The governed result is ready for review."
    assert "list_platform_operations" in provider.tool_names


def test_graph_does_not_auto_select_when_two_data_maps_are_named(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(
        requested_tool="prepare_operation_action",
        arguments={
            "operation_code": "data-maps",
            "objective": "Prepare a Data Map.",
        },
    )
    graph = _orchestrator(
        tmp_path,
        provider,
        operation_catalog=_NormalizedDataMapCatalog(),
    )

    result = graph.invoke(
        conversation_id="conversation-ambiguous-data-map",
        user_id=7,
        messages=(
            _message(
                "Compare Product Revenue to Reporting and Workforce to "
                "Reporting before I choose a data map."
            ),
        ),
    )

    assert result.clarification_request is not None
    assert result.input_request is None


def test_graph_resolves_first_from_data_maps_named_by_previous_assistant(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(
        requested_tool="prepare_operation_action",
        arguments={
            "operation_code": "data-maps",
            "objective": "Push compensation data.",
        },
    )
    graph = _orchestrator(
        tmp_path,
        provider,
        operation_catalog=_CompensationDataMapCatalog(),
    )
    created_at = datetime.now(UTC)

    result = graph.invoke(
        conversation_id="conversation-ordinal-data-map",
        user_id=7,
        messages=(
            AgentMessage(
                message_id=1,
                conversation_id="conversation-ordinal-data-map",
                role=AgentMessageRole.ASSISTANT,
                content=(
                    "I found these Data Maps:\n"
                    "1. OWP_Compensation Data\n"
                    "2. OWP_Compensation Data for Reporting"
                ),
                created_at=created_at,
            ),
            AgentMessage(
                message_id=2,
                conversation_id="conversation-ordinal-data-map",
                role=AgentMessageRole.USER,
                content="first",
                created_at=created_at,
            ),
        ),
    )

    assert result.clarification_request is None
    assert result.input_request is not None
    assert result.input_request.operation_code == "data-maps"
    assert result.input_request.artifact_name == "OWP_Compensation Data"


def test_graph_does_not_infer_shorter_name_from_longer_prior_choice(
    tmp_path: Path,
) -> None:
    choices = (
        "OWP_Compensation Data",
        "OWP_Compensation Data for Reporting",
    )

    selected = AgentGraphOrchestrator._artifact_selected_by_ordinal_reply(
        choices,
        "first",
        "Only OWP_Compensation Data for Reporting was found.",
    )

    assert selected == "OWP_Compensation Data for Reporting"


def test_graph_routes_explicit_data_push_to_data_maps_when_model_selects_import(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(
        requested_tool="prepare_operation_action",
        arguments={
            "operation_code": "data-import",
            "objective": "Push approved revenue data.",
        },
    )
    graph = _orchestrator(
        tmp_path,
        provider,
        operation_catalog=_DataPushRoutingCatalog(),
    )

    result = graph.invoke(
        conversation_id="conversation-data-push-routing",
        user_id=7,
        messages=(
            _message(
                "Push data using Product Revenue to Reporting data map."
            ),
        ),
    )

    assert result.clarification_request is None
    assert result.input_request is not None
    assert result.input_request.operation_code == "data-maps"
    assert result.input_request.artifact_name == (
        "Product_Revenue_to_Reporting"
    )


def test_graph_keeps_explicit_file_import_as_data_import(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(
        requested_tool="prepare_operation_action",
        arguments={
            "operation_code": "data-import",
            "objective": "Import a data file into Planning.",
        },
    )
    graph = _orchestrator(
        tmp_path,
        provider,
        operation_catalog=_DataPushRoutingCatalog(),
    )

    result = graph.invoke(
        conversation_id="conversation-data-import-routing",
        user_id=7,
        messages=(
            _message(
                "Import data from a file using the Revenue Load Job."
            ),
        ),
    )

    assert result.clarification_request is None
    assert result.approval_request is None
    assert result.input_request is not None
    assert result.input_request.operation_code == "data-import"
    assert result.input_request.artifact_name == "Revenue_Load_Job"
    assert result.input_request.context == {
        "allowed_extensions": [".csv", ".txt", ".zip"]
    }


def test_graph_resumes_data_import_upload_into_one_approval(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(
        requested_tool="prepare_operation_action",
        arguments={
            "operation_code": "data-import",
            "objective": "Import the approved forecast file.",
            "artifact_name": "Revenue_Load_Job",
        },
    )
    graph = _orchestrator(
        tmp_path,
        provider,
        operation_catalog=_DataPushRoutingCatalog(),
    )
    paused = graph.invoke(
        conversation_id="conversation-data-import-upload",
        user_id=7,
        messages=(_message("Import data using Revenue Load Job."),),
    )

    assert paused.input_request is not None
    approval = graph.resume_input(
        conversation_id="conversation-data-import-upload",
        user_id=7,
        request_id=paused.input_request.request_id,
        values={
            "file_choice": {
                "source": "upload",
                "upload_token": "upload-token-1",
                "filename": "Forecast.csv",
            },
            "error_file_name": "Forecast_Errors.log",
        },
    )

    assert approval.approval_request is not None
    assert approval.approval_request.operation_code == "data-import"
    assert approval.approval_request.input_values == {
        "file_source": "Upload on governed screen",
        "inbox_file": "",
        "upload_token": "upload-token-1",
        "upload_name": "Forecast.csv",
        "error_file_name": "Forecast_Errors.log",
    }


def test_graph_auto_selects_one_dominant_data_import_match(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(
        requested_tool="prepare_operation_action",
        arguments={
            "operation_code": "data-import",
            "objective": "Load the monthly revenue forecast file.",
        },
    )
    graph = _orchestrator(
        tmp_path,
        provider,
        operation_catalog=_DataImportRecommendationCatalog(
            (
                "Import Revenue Forecast",
                "Import Workforce Actuals",
            )
        ),
    )

    result = graph.invoke(
        conversation_id="conversation-auto-data-import",
        user_id=7,
        messages=(_message("Load the monthly revenue forecast file."),),
    )

    assert result.clarification_request is None
    assert result.input_request is not None
    assert result.input_request.artifact_name == "Import Revenue Forecast"


def test_graph_suggests_ambiguous_data_import_matches(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(
        requested_tool="prepare_operation_action",
        arguments={
            "operation_code": "data-import",
            "objective": "Import the sales file.",
        },
    )
    graph = _orchestrator(
        tmp_path,
        provider,
        operation_catalog=_DataImportRecommendationCatalog(
            (
                "Import Sales Forecast",
                "Import Sales Actuals",
            )
        ),
    )

    result = graph.invoke(
        conversation_id="conversation-suggest-data-import",
        user_id=7,
        messages=(_message("Import the sales file."),),
    )

    assert result.input_request is None
    assert result.clarification_request is not None
    assert tuple(
        item["name"] for item in result.clarification_request.recommendations
    ) == ("Import Sales Actuals", "Import Sales Forecast")


def test_graph_collects_metadata_import_and_conditional_refresh_inputs(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(
        requested_tool="prepare_operation_action",
        arguments={
            "operation_code": "metadata-import",
            "objective": "Import the approved product hierarchy.",
            "artifact_name": "Import Products",
        },
    )
    graph = _orchestrator(
        tmp_path,
        provider,
        operation_catalog=_MetadataImportOperationCatalog(),
    )
    paused = graph.invoke(
        conversation_id="conversation-metadata-import",
        user_id=7,
        messages=(_message("Run Import Products metadata job."),),
    )

    assert paused.input_request is not None
    assert paused.input_request.context["refresh_jobs"] == ["Refresh_Cube"]
    approval = graph.resume_input(
        conversation_id="conversation-metadata-import",
        user_id=7,
        request_id=paused.input_request.request_id,
        values={
            "file_choice": {
                "source": "upload",
                "upload_token": "metadata-upload-1",
                "filename": "Products.csv",
            },
            "error_file_name": "Metadata_Errors.csv",
            "refresh_after_import": True,
            "refresh_job_name": "Refresh_Cube",
        },
    )

    assert approval.approval_request is not None
    assert approval.approval_request.input_values == {
        "file_source": "Upload on governed screen",
        "inbox_file": "",
        "upload_token": "metadata-upload-1",
        "upload_name": "Products.csv",
        "error_file_name": "Metadata_Errors.csv",
        "refresh_after_import": True,
        "refresh_job_name": "Refresh_Cube",
    }
    assert provider.tool_names == ()


def test_graph_accepts_exact_metadata_refresh_job_when_catalog_is_hidden(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(
        requested_tool="prepare_operation_action",
        arguments={
            "operation_code": "metadata-import",
            "objective": "Import the approved product hierarchy.",
            "artifact_name": "Import Products",
        },
    )
    graph = _orchestrator(
        tmp_path,
        provider,
        operation_catalog=_MetadataImportRecommendationCatalog(
            ("Import Products",)
        ),
    )
    paused = graph.invoke(
        conversation_id="conversation-metadata-hidden-refresh",
        user_id=7,
        messages=(_message("Run Import Products metadata job."),),
    )

    assert paused.input_request is not None
    assert paused.input_request.context["refresh_jobs"] == []
    approval = graph.resume_input(
        conversation_id="conversation-metadata-hidden-refresh",
        user_id=7,
        request_id=paused.input_request.request_id,
        values={
            "file_choice": {"source": "configured"},
            "error_file_name": "",
            "refresh_after_import": True,
            "refresh_job_name": "Refresh_Cube",
        },
    )

    assert approval.approval_request is not None
    assert approval.approval_request.input_values["refresh_job_name"] == (
        "Refresh_Cube"
    )


def test_graph_carries_confirmed_metadata_import_from_previous_turn(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(requested_tool="list_platform_operations")
    graph = _orchestrator(
        tmp_path,
        provider,
        operation_catalog=_MetadataImportOperationCatalog(),
    )
    created_at = datetime.now(UTC)

    result = graph.invoke(
        conversation_id="conversation-confirm-metadata-import",
        user_id=7,
        messages=(
            AgentMessage(
                message_id=1,
                conversation_id="conversation-confirm-metadata-import",
                role=AgentMessageRole.USER,
                content="Which Metadata Import job loads products?",
                created_at=created_at,
            ),
            AgentMessage(
                message_id=2,
                conversation_id="conversation-confirm-metadata-import",
                role=AgentMessageRole.ASSISTANT,
                content=(
                    "The matching saved job is Import Products. "
                    "Would you like me to prepare it?"
                ),
                created_at=created_at,
            ),
            AgentMessage(
                message_id=3,
                conversation_id="conversation-confirm-metadata-import",
                role=AgentMessageRole.USER,
                content="yes prepare",
                created_at=created_at,
            ),
        ),
    )

    assert result.clarification_request is None
    assert result.input_request is not None
    assert result.input_request.operation_code == "metadata-import"
    assert result.input_request.artifact_name == "Import Products"
    assert result.input_request.context["refresh_jobs"] == ["Refresh_Cube"]
    assert provider.tool_names == ()


def test_metadata_import_guidance_still_uses_the_model(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(requested_tool="list_platform_operations")
    graph = _orchestrator(
        tmp_path,
        provider,
        operation_catalog=_MetadataImportOperationCatalog(),
    )

    result = graph.invoke(
        conversation_id="conversation-metadata-import-guidance",
        user_id=7,
        messages=(_message("How does Metadata Import work?"),),
    )

    assert result.text == "The governed result is ready for review."
    assert "list_platform_operations" in provider.tool_names


def test_graph_auto_selects_one_dominant_metadata_import_match(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(
        requested_tool="prepare_operation_action",
        arguments={
            "operation_code": "metadata-import",
            "objective": "Load the approved product hierarchy.",
        },
    )
    graph = _orchestrator(
        tmp_path,
        provider,
        operation_catalog=_MetadataImportRecommendationCatalog(
            ("Import Product Hierarchy", "Import Customer Attributes")
        ),
    )

    result = graph.invoke(
        conversation_id="conversation-auto-metadata-import",
        user_id=7,
        messages=(_message("Load the approved product hierarchy."),),
    )

    assert result.clarification_request is None
    assert result.input_request is not None
    assert result.input_request.artifact_name == "Import Product Hierarchy"


def test_graph_suggests_ambiguous_metadata_import_matches(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(
        requested_tool="prepare_operation_action",
        arguments={
            "operation_code": "metadata-import",
            "objective": "Import product metadata.",
        },
    )
    graph = _orchestrator(
        tmp_path,
        provider,
        operation_catalog=_MetadataImportRecommendationCatalog(
            ("Import Product Hierarchy", "Import Product Attributes")
        ),
    )

    result = graph.invoke(
        conversation_id="conversation-suggest-metadata-import",
        user_id=7,
        messages=(_message("Import product metadata."),),
    )

    assert result.input_request is None
    assert result.clarification_request is not None
    assert len(result.clarification_request.recommendations) == 2


def test_graph_never_approves_metadata_import_without_a_live_job(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(
        requested_tool="prepare_operation_action",
        arguments={
            "operation_code": "metadata-import",
            "objective": "Import product metadata.",
        },
    )
    graph = _orchestrator(
        tmp_path,
        provider,
        operation_catalog=_MetadataImportRecommendationCatalog(()),
    )

    with pytest.raises(AgentProviderError, match="could not resolve"):
        graph.invoke(
            conversation_id="conversation-missing-metadata-import",
            user_id=7,
            messages=(_message("Import product metadata."),),
        )


def test_graph_prepares_exact_live_cube_refresh_for_one_approval(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(
        requested_tool="prepare_operation_action",
        arguments={
            "operation_code": "cube-refresh",
            "objective": "Synchronize the Planning cube after metadata changes.",
            "artifact_name": "Refresh_Cube",
        },
    )
    graph = _orchestrator(
        tmp_path,
        provider,
        operation_catalog=_CubeRefreshOperationCatalog(),
    )

    result = graph.invoke(
        conversation_id="conversation-cube-refresh",
        user_id=7,
        messages=(
            _message("Run the Refresh_Cube job after approved metadata changes."),
        ),
    )

    assert result.clarification_request is None
    assert result.input_request is None
    assert result.approval_request is not None
    assert result.approval_request.operation_code == "cube-refresh"
    assert result.approval_request.artifact_name == "Refresh_Cube"
    assert result.approval_request.input_values == {}
    assert "application" in result.approval_request.effect.casefold()


def test_graph_collects_live_substitution_variable_update_for_one_approval(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(
        requested_tool="prepare_operation_action",
        arguments={
            "operation_code": "substitution-variables",
            "objective": "Move the current Planning year to FY27.",
            "artifact_name": "CurYr",
        },
    )
    graph = _orchestrator(
        tmp_path,
        provider,
        substitution_variables=_SubstitutionVariables(),
    )

    paused = graph.invoke(
        conversation_id="conversation-variable-update",
        user_id=7,
        messages=(_message("Update CurYr to FY27."),),
    )

    assert paused.clarification_request is None
    assert paused.input_request is not None
    assert paused.input_request.context == {
        "action": "UPDATE",
        "scope": "ALL",
        "variable_name": "CurYr",
        "current_value": "FY26",
        "prefill": {"new_value": "FY27"},
    }
    assert provider.tool_names == ()
    approval = graph.resume_input(
        conversation_id="conversation-variable-update",
        user_id=7,
        request_id=paused.input_request.request_id,
        values={"new_value": "FY27"},
    )

    assert approval.approval_request is not None
    assert approval.approval_request.operation_code == "substitution-variables"
    assert approval.approval_request.input_values == {
        "action": "UPDATE",
        "scope": "ALL",
        "variable_name": "CurYr",
        "new_value": "FY27",
        "expected_current_value": "FY26",
        "create_if_missing": False,
    }
    assert "changed after selection" in approval.approval_request.effect


def test_substitution_variable_update_never_falls_back_to_create(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(
        requested_tool="prepare_operation_action",
        arguments={
            "operation_code": "substitution-variables",
            "objective": "Update CurYr to FY27.",
            # A model may incorrectly suggest this generic catalog action.
            "artifact_name": "Create a new substitution variable",
        },
    )
    graph = _orchestrator(
        tmp_path,
        provider,
        substitution_variables=_DuplicateSubstitutionVariables(),
    )

    paused = graph.invoke(
        conversation_id="conversation-variable-scoped-update",
        user_id=7,
        messages=(
            _message("Update the CurYr substitution variable to FY27."),
        ),
    )

    assert paused.input_request is None
    assert paused.clarification_request is not None
    assert paused.clarification_request.options == (
        "ALL.CurYr",
        "Plan1.CurYr",
    )
    assert "Create a new substitution variable" not in (
        paused.clarification_request.options
    )


def test_existing_substitution_variable_overrides_incorrect_create_suggestion(
    tmp_path: Path,
) -> None:
    graph = _orchestrator(
        tmp_path,
        _StepProvider(
            requested_tool="prepare_operation_action",
            arguments={
                "operation_code": "substitution-variables",
                "objective": "Update CurYr to FY27.",
                "artifact_name": "Create a new substitution variable",
            },
        ),
        substitution_variables=_SubstitutionVariables(),
    )

    paused = graph.invoke(
        conversation_id="conversation-existing-variable-not-create",
        user_id=7,
        messages=(
            _message("Update the CurYr substitution variable to FY27."),
        ),
    )

    assert paused.clarification_request is None
    assert paused.input_request is not None
    assert paused.input_request.artifact_name == "CurYr"
    assert paused.input_request.context == {
        "action": "UPDATE",
        "scope": "ALL",
        "variable_name": "CurYr",
        "current_value": "FY26",
        "prefill": {"new_value": "FY27"},
    }


def test_application_scope_remains_valid_when_plan_types_are_unavailable(
    tmp_path: Path,
) -> None:
    gateway = AgentCapabilityGateway(
        _settings(tmp_path),
        control_center=_ControlCenter(),
        data_review=_DataReview(),
        substitution_variables=_EmptyScopeSubstitutionVariables(),
    )

    normalized = gateway.normalize_guided_inputs(
        "substitution-variables",
        "Create a new substitution variable",
        {
            "scope": " ALL ",
            "variable_name": "FcstYr",
            "new_value": "FY27",
        },
    )

    assert normalized["scope"] == "ALL"
    assert normalized["variable_name"] == "FcstYr"


def test_graph_can_cancel_substitution_variable_input(
    tmp_path: Path,
) -> None:
    graph = _orchestrator(
        tmp_path,
        _StepProvider(
            requested_tool="prepare_operation_action",
            arguments={
                "operation_code": "substitution-variables",
                "objective": "Update CurYr to FY27.",
                "artifact_name": "CurYr",
            },
        ),
        substitution_variables=_SubstitutionVariables(),
    )
    paused = graph.invoke(
        conversation_id="conversation-variable-cancel",
        user_id=7,
        messages=(_message("Update CurYr to FY27."),),
    )
    assert paused.input_request is not None

    completed = graph.resume_input(
        conversation_id="conversation-variable-cancel",
        user_id=7,
        request_id=paused.input_request.request_id,
        values=None,
    )

    assert completed.input_request is None
    assert completed.approval_request is None
    assert completed.tool_activity[0].status == "CANCELLED"


def test_multi_step_intent_preserves_business_sequence() -> None:
    steps = AgentGraphOrchestrator._requested_multi_step_codes(
        "Import metadata, then load data, calculate the forecast, and push "
        "data to reporting."
    )

    assert steps == (
        "metadata-import",
        "data-import",
        "business-rules",
        "data-maps",
    )


def test_multi_step_intent_includes_pipeline_and_variable_operations() -> None:
    steps = AgentGraphOrchestrator._requested_multi_step_codes(
        "Run Oracle Pipeline PIPE01, then update substitution variable CurYr, "
        "then run Business Rule Calculate Forecast."
    )

    assert steps == (
        "pipelines",
        "substitution-variables",
        "business-rules",
    )


def test_data_integration_name_is_not_also_a_native_data_import() -> None:
    steps = AgentGraphOrchestrator._requested_multi_step_codes(
        "Run Revenue Load Data Integration."
    )

    assert steps == ("data-integrations",)


def test_separate_integration_and_native_data_import_are_both_preserved() -> None:
    steps = AgentGraphOrchestrator._requested_multi_step_codes(
        "Run the Data Integration, then load data with the native import job."
    )

    assert steps == ("data-integrations", "data-import")


def test_pipeline_does_not_preempt_a_multi_operation_request(
    tmp_path: Path,
) -> None:
    graph = _orchestrator(
        tmp_path,
        _NeverCalledProvider(),
        operation_catalog=_PipelineOperationCatalog(),
    )

    result = graph.invoke(
        conversation_id="conversation-pipeline-then-rule",
        user_id=7,
        messages=(
            _message(
                "Run Oracle Pipeline PIPE01, then Business Rule "
                "Calculate Forecast."
            ),
        ),
    )

    assert result.tool_activity[0].name == "plan_multi_step_request"
    assert result.tool_activity[0].result["resolution"] == (
        "STANDALONE_FLOW_DRAFT"
    )
    assert [
        item["code"]
        for item in result.tool_activity[0].result["requested_steps"]
    ] == ["pipelines", "business-rules"]


def test_multi_step_parser_resolves_exact_artifacts_across_catalogs(
    tmp_path: Path,
) -> None:
    graph = _orchestrator(
        tmp_path,
        _NeverCalledProvider(),
        operation_catalog=_RepeatedRuleFlowOperationCatalog(),
    )

    call = graph._deterministic_multi_step_plan_call(
        {
            "messages": [
                {
                    "role": AgentMessageRole.USER.value,
                    "content": (
                        "Run Revenue_Load_v2, then BR_Calculate_Revenue_v2, "
                        "then BR_Aggregate_Forecast_v2."
                    ),
                }
            ],
            "allowed_tool_names": ["plan_multi_step_request"],
        }
    )

    assert call is not None
    assert call.arguments["requested_steps"] == [
        "data-integrations",
        "business-rules",
        "business-rules",
    ]


def test_graph_plans_multi_step_request_without_model_or_oracle_writes(
    tmp_path: Path,
) -> None:
    graph = _orchestrator(
        tmp_path,
        _NeverCalledProvider(),
        operation_catalog=_PipelineOperationCatalog(),
    )

    result = graph.invoke(
        conversation_id="conversation-multi-step-plan",
        user_id=7,
        messages=(
            _message("Load forecast data and calculate the revenue forecast."),
        ),
    )

    assert result.approval_request is None
    assert result.tool_activity[0].name == "plan_multi_step_request"
    assert result.tool_activity[0].status == "SUCCESS"
    assert result.tool_activity[0].result["resolution"] == "ORACLE_PIPELINE"
    assert result.tool_activity[0].result["pipeline"]["code"] == "PIPE01"
    assert "Monthly Forecast" in result.text


def test_graph_honors_standalone_flow_choice_without_reselecting_pipeline(
    tmp_path: Path,
) -> None:
    graph = _orchestrator(
        tmp_path,
        _NeverCalledProvider(),
        operation_catalog=_PipelineOperationCatalog(),
    )

    result = graph.invoke(
        conversation_id="conversation-standalone-flow",
        user_id=7,
        messages=(
            _message(
                "Prepare a standalone flow without an Oracle Pipeline for "
                "these operations: Data Import -> Business Rules. Objective: "
                "load forecast data and calculate revenue."
            ),
        ),
    )

    assert result.approval_request is None
    activity = result.tool_activity[0]
    assert activity.name == "plan_multi_step_request"
    assert activity.arguments["prefer_standalone"] is True
    assert activity.result["resolution"] == "STANDALONE_FLOW_DRAFT"
    assert activity.result["pipeline"] is None
    assert "standalone flow draft" in result.text.casefold()


def test_graph_configures_and_approves_executable_standalone_flow(
    tmp_path: Path,
) -> None:
    graph = _orchestrator(
        tmp_path,
        _NeverCalledProvider(),
        operation_catalog=_StandaloneFlowOperationCatalog(),
    )
    conversation_id = "conversation-executable-standalone-flow"

    first = graph.invoke(
        conversation_id=conversation_id,
        user_id=7,
        messages=(
            _message(
                "Configure and execute a standalone flow without an Oracle "
                "Pipeline for these operations: Business Rules -> Data Maps."
            ),
        ),
        task_context={
            "intent": "MONTH_CLOSE",
            "phase": "READY_FOR_PLAN",
            "confidence": "HIGH_CONFIDENCE",
            "parameters": {
                "period": "Sep",
                "activities": ["Business Rules", "Data Maps"],
            },
            "missing_parameters": [],
            "clarification_prompt": None,
            "objective": "Start September month close",
        },
    )

    assert first.clarification_request is None, first.clarification_request
    assert first.input_request is not None
    assert first.input_request.operation_code == "business-rules"
    assert first.input_request.context["flow_step"] == {
        "sequence": 1,
        "total": 2,
    }
    second = graph.resume_input(
        conversation_id=conversation_id,
        user_id=7,
        request_id=first.input_request.request_id,
        values={
            "runtime_prompt_mode": "Use Calculation Manager defaults",
            "runtime_prompts": {},
        },
    )
    assert second.input_request is not None
    assert second.input_request.operation_code == "data-maps"
    assert second.input_request.context["flow_step"] == {
        "sequence": 2,
        "total": 2,
    }
    review = graph.resume_input(
        conversation_id=conversation_id,
        user_id=7,
        request_id=second.input_request.request_id,
        values={
            "clear_target": False,
            "member_overrides": {},
            "exclusion_overrides": {},
        },
    )

    assert review.approval_request is not None
    assert review.approval_request.operation_code == "standalone-flow"
    steps = review.approval_request.input_values["steps"]
    assert [item["artifact_name"] for item in steps] == [
        "Calculate Forecast",
        "Forecast to Reporting",
    ]
    completed = graph.resume_approval(
        conversation_id=conversation_id,
        user_id=7,
        request_id=review.approval_request.request_id,
        decision="approve",
    )

    assert completed.tool_activity[0].name == (
        "prepare_standalone_flow_action"
    )
    assert completed.tool_activity[0].result["standalone_flow"]["status"] == (
        "READY_FOR_APPROVAL"
    )


def test_month_close_standalone_choice_advances_to_live_artifact_selection(
    tmp_path: Path,
) -> None:
    graph = _orchestrator(
        tmp_path,
        _NeverCalledProvider(),
        operation_catalog=_CompleteStandaloneFlowOperationCatalog(),
    )

    result = graph.invoke(
        conversation_id="conversation-month-close-standalone-choice",
        user_id=7,
        messages=(
            _message("Start September month close."),
            _message("Run Data Integration, then Business Rule, then Data Map."),
            _message(
                "Configure and execute a standalone flow without an Oracle "
                "Pipeline for these operations: Data Integrations -> Business "
                "Rules -> Data Maps. Objective: Sep Month Close."
            ),
        ),
        task_context={
            "intent": "MONTH_CLOSE",
            "phase": "READY_FOR_PLAN",
            "confidence": "HIGH_CONFIDENCE",
            "parameters": {
                "period": "Sep",
                "activities": [
                    "Data Integration",
                    "Business Rule",
                    "Data Map",
                ],
            },
            "missing_parameters": [],
            "clarification_prompt": None,
            "objective": "Start September month close",
        },
    )

    assert result.input_request is not None
    assert result.input_request.operation_code == "data-integrations"
    assert result.input_request.artifact_name == "Revenue Load"
    assert result.input_request.context["flow_step"] == {
        "sequence": 1,
        "total": 3,
    }
    assert result.tool_activity == ()


def test_explicit_forecast_seed_lists_live_rule_choices_and_synonyms(
    tmp_path: Path,
) -> None:
    graph = _orchestrator(
        tmp_path,
        _NeverCalledProvider(),
        operation_catalog=_ForecastSeedRuleCatalog(),
    )
    message = _message("Run forecast seeding")
    conversation_id = "conversation-explicit-forecast-seed"

    choice = graph.invoke(
        conversation_id=conversation_id,
        user_id=7,
        messages=(message,),
        task_context=AgentTaskInterpreter.interpret((message,)).to_payload(),
    )

    assert choice.clarification_request is not None
    assert choice.clarification_request.operation_code == "business-rules"
    assert set(choice.clarification_request.options) == {
        "Actual to Forecast",
        "Plan to Forecast",
        "Create Forecast",
        "Aggregate Forecast",
    }
    recommended = choice.clarification_request.recommendations
    assert {item["name"] for item in recommended[:2]} == {
        "Actual to Forecast", "Plan to Forecast"
    }
    assert all(item["confidence"] == "Possible match" for item in recommended)

    selected = graph.resume_clarification(
        conversation_id=conversation_id,
        user_id=7,
        request_id=choice.clarification_request.request_id,
        value="Plan to Forecast",
    )
    assert selected.input_request is not None
    assert selected.input_request.operation_code == "business-rules"
    assert selected.input_request.artifact_name == "Plan to Forecast"


def test_forecast_seeding_asks_for_the_approved_live_method_when_ambiguous(
    tmp_path: Path,
) -> None:
    graph = _orchestrator(
        tmp_path,
        _NeverCalledProvider(),
        operation_catalog=_ForecastSeedingOperationCatalog(),
    )

    result = graph.invoke(
        conversation_id="conversation-forecast-method",
        user_id=7,
        messages=(
            _message("Prepare the new forecast."),
            _message("Use actuals through August."),
        ),
        task_context={
            "intent": "FORECAST_SEEDING",
            "phase": "READY_FOR_PLAN",
            "confidence": "HIGH_CONFIDENCE",
            "parameters": {"cutoff_period": "Aug"},
            "missing_parameters": [],
            "clarification_prompt": None,
            "objective": "Prepare the new forecast",
        },
    )

    assert "more than one approved Oracle route" in result.text
    assert "Oracle Pipeline" in result.text
    assert "Business Rule" in result.text
    assert "Data Integration" in result.text
    assert result.tool_activity == ()


def test_forecast_seeding_method_advances_to_live_artifact_and_inputs(
    tmp_path: Path,
) -> None:
    graph = _orchestrator(
        tmp_path,
        _NeverCalledProvider(),
        operation_catalog=_ForecastSeedingOperationCatalog(),
    )
    conversation_id = "conversation-forecast-rule"

    choice = graph.invoke(
        conversation_id=conversation_id,
        user_id=7,
        messages=(
            _message("Prepare the new forecast."),
            _message("Use actuals through August."),
            _message("Use the Business Rule."),
        ),
        task_context={
            "intent": "FORECAST_SEEDING",
            "phase": "READY_FOR_PLAN",
            "confidence": "HIGH_CONFIDENCE",
            "parameters": {
                "cutoff_period": "Aug",
                "execution_method": "BUSINESS_RULE",
            },
            "missing_parameters": [],
            "clarification_prompt": None,
            "objective": "Prepare the new forecast",
        },
    )

    assert choice.clarification_request is not None
    assert choice.clarification_request.operation_code == "business-rules"
    assert choice.clarification_request.options == ("Seed Forecast",)
    inputs = graph.resume_clarification(
        conversation_id=conversation_id,
        user_id=7,
        request_id=choice.clarification_request.request_id,
        value="Seed Forecast",
    )
    assert inputs.input_request is not None
    assert inputs.input_request.operation_code == "business-rules"
    assert inputs.input_request.context["task_context"] == {
        "cutoff_period": "Aug",
        "execution_method": "BUSINESS_RULE",
    }


def test_variance_reporting_lists_saved_layouts_before_reading_data(
    tmp_path: Path,
) -> None:
    graph = _orchestrator(
        tmp_path,
        _NeverCalledProvider(),
        report_workspace=_ReportWorkspace(),
    )

    result = graph.invoke(
        conversation_id="conversation-variance-layouts",
        user_id=7,
        messages=(_message("Show September Actual vs Budget variance."),),
        allowed_tool_names=("list_variance_views", "review_saved_variance"),
        task_context={
            "intent": "VARIANCE_REPORTING",
            "phase": "READY_FOR_PLAN",
            "confidence": "HIGH_CONFIDENCE",
            "parameters": {
                "comparison": "Actual vs Budget",
                "period": "Sep",
            },
            "missing_parameters": [],
            "clarification_prompt": None,
            "objective": "Show September Actual vs Budget variance.",
        },
    )

    assert result.tool_activity[0].name == "list_variance_views"
    assert result.tool_activity[0].result["purpose"] == "variance"
    assert "Choose one below" in result.text


def test_variance_reporting_uses_selected_layout_for_live_comparison(
    tmp_path: Path,
) -> None:
    graph = _orchestrator(
        tmp_path,
        _NeverCalledProvider(),
        data_review=_VarianceDataReview(),
        report_workspace=_ReportWorkspace(),
    )

    result = graph.invoke(
        conversation_id="conversation-variance-review",
        user_id=7,
        messages=(
            _message("Show September Actual vs Budget variance above 500 for FY26."),
            _message(
                "Use saved Data Explorer view `revenue-forecast` for the variance review."
            ),
        ),
        allowed_tool_names=("list_variance_views", "review_saved_variance"),
        task_context={
            "intent": "VARIANCE_REPORTING",
            "phase": "READY_FOR_PLAN",
            "confidence": "HIGH_CONFIDENCE",
            "parameters": {
                "comparison": "Actual vs Budget",
                "period": "Sep",
                "year": "FY26",
                "threshold": 500,
                "saved_view": "revenue-forecast",
                "pov_overrides": {"Product": "Snacks"},
            },
            "missing_parameters": [],
            "clarification_prompt": None,
            "objective": "Show September Actual vs Budget variance above 500 for FY26.",
        },
    )

    assert result.tool_activity[0].name == "review_saved_variance"
    assert result.tool_activity[0].arguments["pov_overrides"] == {
        "Product": "Snacks"
    }
    assert result.tool_activity[0].result["result"]["compared_cells"] == 3
    assert "found **1**" in result.text


def test_standalone_flow_input_resume_returns_next_artifact_choice(
    tmp_path: Path,
) -> None:
    """A valid next-step clarification must not look like an empty response."""
    graph = _orchestrator(
        tmp_path,
        _NeverCalledProvider(),
        operation_catalog=_RepeatedRuleFlowOperationCatalog(),
    )
    conversation_id = "conversation-flow-input-to-rule-choice"

    first = graph.invoke(
        conversation_id=conversation_id,
        user_id=7,
        messages=(
            _message(
                "Configure and execute a standalone flow without an Oracle "
                "Pipeline for these operations: Data Import -> Business "
                "Rules. Use Import Forecast Data, then calculate the plan."
            ),
        ),
    )

    assert first.input_request is not None
    assert first.input_request.operation_code == "data-import"
    next_step = graph.resume_input(
        conversation_id=conversation_id,
        user_id=7,
        request_id=first.input_request.request_id,
        values={
            "file_choice": {"source": "configured"},
            "error_file_name": "",
        },
    )

    assert next_step.clarification_request is not None
    assert next_step.clarification_request.operation_code == "business-rules"
    assert next_step.clarification_request.options == (
        "BR_Calculate_Revenue_v2",
        "BR_Aggregate_Forecast_v2",
    )


def test_standalone_flow_preserves_two_distinct_renamed_business_rules(
    tmp_path: Path,
) -> None:
    graph = _orchestrator(
        tmp_path,
        _NeverCalledProvider(),
        operation_catalog=_RepeatedRuleFlowOperationCatalog(),
    )
    conversation_id = "conversation-repeated-renamed-rules"

    integration = graph.invoke(
        conversation_id=conversation_id,
        user_id=7,
        messages=(
            _message(
                "Configure and execute a standalone flow without an Oracle "
                "Pipeline. Run Data Integration Revenue_Load_v2, then "
                "Business Rule BR_Calculate_Revenue_v2, then Business Rule "
                "BR_Aggregate_Forecast_v2."
            ),
        ),
    )

    assert integration.input_request is not None
    assert integration.input_request.operation_code == "data-integrations"
    first_rule = graph.resume_input(
        conversation_id=conversation_id,
        user_id=7,
        request_id=integration.input_request.request_id,
        values={
            "start_period": "Jan-27",
            "end_period": "Jan-27",
            "import_mode": "Replace",
            "export_mode": "Merge",
            "file_choice": {"source": "configured"},
        },
    )
    assert first_rule.input_request is not None
    assert first_rule.input_request.artifact_name == "BR_Calculate_Revenue_v2"

    second_rule = graph.resume_input(
        conversation_id=conversation_id,
        user_id=7,
        request_id=first_rule.input_request.request_id,
        values={
            "runtime_prompt_mode": "Use Calculation Manager defaults",
            "runtime_prompts": {},
        },
    )
    assert second_rule.input_request is not None
    assert second_rule.input_request.artifact_name == "BR_Aggregate_Forecast_v2"

    review = graph.resume_input(
        conversation_id=conversation_id,
        user_id=7,
        request_id=second_rule.input_request.request_id,
        values={
            "runtime_prompt_mode": "Use Calculation Manager defaults",
            "runtime_prompts": {},
        },
    )

    assert review.approval_request is not None
    assert [
        (step["operation_code"], step["artifact_name"])
        for step in review.approval_request.input_values["steps"]
    ] == [
        ("data-integrations", "Revenue_Load_v2"),
        ("business-rules", "BR_Calculate_Revenue_v2"),
        ("business-rules", "BR_Aggregate_Forecast_v2"),
    ]


def test_ui_generated_standalone_prompt_does_not_duplicate_objective_steps(
    tmp_path: Path,
) -> None:
    graph = _orchestrator(
        tmp_path,
        _NeverCalledProvider(),
        operation_catalog=_RepeatedRuleFlowOperationCatalog(),
    )

    first = graph.invoke(
        conversation_id="conversation-ui-generated-repeated-rules",
        user_id=7,
        messages=(
            _message(
                "Configure and execute a standalone flow without an Oracle "
                "Pipeline for these operations: Data Integrations -> "
                "Business Rules -> Business Rules. Objective: Run Data "
                "Integration Revenue_Load_v2, then Business Rule "
                "BR_Calculate_Revenue_v2, then Business Rule "
                "BR_Aggregate_Forecast_v2."
            ),
        ),
    )

    assert first.input_request is not None
    assert first.input_request.operation_code == "data-integrations"
    assert first.input_request.context["flow_step"] == {
        "sequence": 1,
        "total": 3,
    }


def test_graph_prefills_exact_user_variable_and_new_member_without_model(
    tmp_path: Path,
) -> None:
    provider = _StepProvider()
    graph = _orchestrator(
        tmp_path,
        provider,
        substitution_variables=_SubstitutionVariables(),
        user_variables=_UserVariables(),
    )

    paused = graph.invoke(
        conversation_id="conversation-user-variable-update",
        user_id=7,
        messages=(
            _message("Set the MyEntity user variable to Sales East."),
        ),
    )

    assert paused.clarification_request is None
    assert paused.input_request is not None
    assert paused.input_request.operation_code == "user-variables"
    assert paused.input_request.artifact_name == "MyEntity"
    assert paused.input_request.context == {
        "variable_name": "MyEntity",
        "dimension": "Entity",
        "prefill": {"new_member": "Sales East"},
    }
    assert provider.tool_names == ()

    approval = graph.resume_input(
        conversation_id="conversation-user-variable-update",
        user_id=7,
        request_id=paused.input_request.request_id,
        values={"user_name": "service-user", "new_member": "Sales East"},
    )

    assert approval.approval_request is not None
    assert approval.approval_request.input_values == {
        "user_name": "service-user",
        "variable_name": "MyEntity",
        "dimension": "Entity",
        "new_member": "Sales East",
        "expected_current_member": "Sales West",
    }


def test_graph_collects_explicit_substitution_variable_creation(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(
        requested_tool="prepare_operation_action",
        arguments={
            "operation_code": "substitution-variables",
            "objective": "Create a new substitution variable.",
            "artifact_name": "Create a new substitution variable",
        },
    )
    graph = _orchestrator(
        tmp_path,
        provider,
        substitution_variables=_SubstitutionVariables(),
    )

    paused = graph.invoke(
        conversation_id="conversation-variable-create",
        user_id=7,
        messages=(_message("Create a new substitution variable."),),
    )

    assert paused.input_request is not None
    assert paused.input_request.context["action"] == "CREATE"
    approval = graph.resume_input(
        conversation_id="conversation-variable-create",
        user_id=7,
        request_id=paused.input_request.request_id,
        values={
            "scope": "Plan1",
            "variable_name": "FcstYr",
            "new_value": "FY27",
        },
    )

    assert approval.approval_request is not None
    assert approval.approval_request.input_values["action"] == "CREATE"
    assert approval.approval_request.input_values["scope"] == "Plan1"
    assert approval.approval_request.input_values["variable_name"] == "FcstYr"


def test_graph_reuses_explicit_substitution_variable_name_and_value(
    tmp_path: Path,
) -> None:
    provider = _StepProvider(
        requested_tool="prepare_operation_action",
        arguments={
            "operation_code": "substitution-variables",
            "objective": "Create NewFcstYr with FY27.",
            "artifact_name": "Create a new substitution variable",
        },
    )
    graph = _orchestrator(
        tmp_path,
        provider,
        substitution_variables=_SubstitutionVariables(),
    )

    result = graph.invoke(
        conversation_id="conversation-variable-prefill",
        user_id=7,
        messages=(
            _message(
                "Create substitution variable NewFcstYr with initial value FY27."
            ),
        ),
    )

    assert result.input_request is None
    assert result.approval_request is not None
    assert result.approval_request.input_values["scope"] == "ALL"
    assert result.approval_request.input_values["variable_name"] == "NewFcstYr"
    assert result.approval_request.input_values["new_value"] == "FY27"


def test_graph_can_cancel_during_artifact_clarification(tmp_path: Path) -> None:
    provider = _StepProvider(
        requested_tool="prepare_operation_action",
        arguments={
            "operation_code": "business-rules",
            "objective": "Calculate the forecast.",
        },
    )
    graph = _orchestrator(
        tmp_path,
        provider,
        operation_catalog=_OperationCatalog(),
    )
    paused = graph.invoke(
        conversation_id="conversation-cancel-choice",
        user_id=7,
        messages=(_message(),),
    )
    assert paused.clarification_request is not None

    completed = graph.resume_clarification(
        conversation_id="conversation-cancel-choice",
        user_id=7,
        request_id=paused.clarification_request.request_id,
        value=None,
    )

    assert completed.tool_activity[0].status == "CANCELLED"


def test_pipeline_missing_from_catalog_can_continue_after_verified_registration(
    tmp_path: Path,
) -> None:
    catalog = _RecoverablePipelineCatalog()
    provider = _StepProvider(
        requested_tool="prepare_operation_action",
        arguments={
            "operation_code": "pipelines",
            "objective": "Run the monthly revenue load.",
        },
    )
    graph = _orchestrator(tmp_path, provider, operation_catalog=catalog)

    paused = graph.invoke(
        conversation_id="conversation-recover-pipeline",
        user_id=7,
        messages=(_message("Run the monthly revenue load Pipeline."),),
    )

    assert paused.clarification_request is not None
    assert paused.clarification_request.options == ()
    assert paused.clarification_request.catalog_recovery[
        "registration_mode"
    ] == "verified"

    catalog.registered = True
    continued = graph.resume_clarification(
        conversation_id="conversation-recover-pipeline",
        user_id=7,
        request_id=paused.clarification_request.request_id,
        value="PIPE_NEW",
    )

    assert continued.input_request is not None
    assert continued.input_request.artifact_name == "PIPE_NEW"


def test_pending_data_integration_registration_can_continue_agent_flow(
    tmp_path: Path,
) -> None:
    catalog = _RecoverableDataIntegrationCatalog()
    provider = _StepProvider(
        requested_tool="prepare_operation_action",
        arguments={
            "operation_code": "data-integrations",
            "objective": "Load the new revenue file.",
        },
    )
    graph = _orchestrator(tmp_path, provider, operation_catalog=catalog)

    paused = graph.invoke(
        conversation_id="conversation-recover-integration",
        user_id=7,
        messages=(_message("Run the new revenue Data Integration."),),
    )

    assert paused.clarification_request is not None
    assert paused.clarification_request.options == ()
    assert paused.clarification_request.catalog_recovery[
        "registration_mode"
    ] == "pending"

    catalog.registered = True
    continued = graph.resume_clarification(
        conversation_id="conversation-recover-integration",
        user_id=7,
        request_id=paused.clarification_request.request_id,
        value="Revenue_Load_New",
    )

    assert continued.input_request is not None
    assert continued.input_request.artifact_name == "Revenue_Load_New"


def test_checkpoint_store_is_reused_across_compiled_graphs(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = AgentCheckpointStore(settings.database_target)

    assert store.get() is store.get()
    store.setup()
    store.close()

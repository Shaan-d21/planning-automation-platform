"""Planning operation contribution with backwards-compatible contracts."""

from __future__ import annotations

from app.products.contracts import (
    BusinessProcessType,
    CapabilityDefinition,
    CapabilityScope,
    NavigationDefinition,
    OperationDefinition,
    OperationKind,
)


PLANNING_CAPABILITIES = (
    CapabilityDefinition(
        code="planning-data-explorer",
        scope=CapabilityScope.PRODUCT,
        description="Review and export governed Planning data slices.",
        agent_tools=(
            "list_planning_cubes",
            "list_cube_dimensions",
            "search_dimension_members",
            "list_data_explorer_views",
            "review_saved_data_view",
            "list_variance_views",
            "review_saved_variance",
            "review_data_slice",
            "compare_data_slices",
        ),
    ),
    CapabilityDefinition(
        code="planning-workflow",
        scope=CapabilityScope.PRODUCT,
        description="Manage Planning work, approvals, and planning cycles.",
        agent_tools=(),
    ),
    CapabilityDefinition(
        code="planning-governed-operations",
        scope=CapabilityScope.PRODUCT,
        description=(
            "Discover, prepare, and schedule governed Planning operations."
        ),
        agent_tools=(
            "list_platform_operations",
            "list_operation_artifacts",
            "plan_multi_step_request",
            "prepare_operation_action",
            "prepare_standalone_flow_action",
            "prepare_schedule_action",
        ),
    ),
)


PLANNING_NAVIGATION_DEFINITIONS = (
    NavigationDefinition("tasks", "My Work", "#tasks", "workspace", 20),
    NavigationDefinition(
        "approvals", "Approvals", "#approvals", "planning", 40, ("PROCESS_RUN",)
    ),
    NavigationDefinition(
        "data-review",
        "Data Explorer",
        "#data-review",
        "planning",
        50,
        ("DATA_REVIEW",),
    ),
    NavigationDefinition(
        "operations",
        "Operations",
        "#operations",
        "automation",
        60,
        ("OPERATION_EXECUTE", "USER_VARIABLE_UPDATE"),
    ),
    NavigationDefinition(
        "schedules",
        "Schedules",
        "#schedules",
        "automation",
        70,
        ("SCHEDULE_MANAGE",),
    ),
    NavigationDefinition(
        "reports",
        "Data Explorer",
        "#reports",
        "planning",
        80,
        ("REPORT_GENERATE",),
    ),
    NavigationDefinition(
        "cycles",
        "Planning Cycles",
        "#cycles",
        "administration",
        110,
        ("PROCESS_DESIGN",),
    ),
)


PLANNING_OPERATION_DEFINITIONS = (
    OperationDefinition(
        kind=OperationKind.REPORT_GENERATION,
        code="report-generation",
        display_name="Data Explorer Export",
        description=(
            "Export an approved saved Data Explorer view and create a "
            "downloadable Excel workbook."
        ),
        category="Analysis",
        risk_level="Read only",
        route="/app/reports",
    ),
    OperationDefinition(
        kind=OperationKind.CUBE_REFRESH,
        code="cube-refresh",
        display_name="Planning Cube Refresh",
        description=(
            "Run and monitor an existing saved Cube Refresh job after "
            "reviewing its application-wide impact."
        ),
        category="Application administration",
        risk_level="Elevated",
        route="/app/operations/cube-refresh",
    ),
    OperationDefinition(
        kind=OperationKind.SUBSTITUTION_VARIABLE,
        code="substitution-variables",
        display_name="Substitution Variables",
        description=(
            "Review application- and cube-scoped variables, safely update "
            "existing values, or explicitly create a new definition."
        ),
        category="Application administration",
        risk_level="Elevated",
        route="/app/operations/substitution-variables",
    ),
    OperationDefinition(
        kind=OperationKind.USER_VARIABLE,
        code="user-variables",
        display_name="User Variables",
        description=(
            "Review live Planning user-variable assignments and safely set "
            "the selected member for yourself or an authorized user."
        ),
        category="User preferences",
        risk_level="Controlled",
        route="/app/operations/user-variables",
    ),
    OperationDefinition(
        kind=OperationKind.DATA_IMPORT,
        code="data-import",
        display_name="Planning Data Import",
        description=(
            "Upload or reuse a data file and run a saved native Planning "
            "Import Data job."
        ),
        category="Data loading",
        risk_level="Elevated",
        route="/app/operations/data-import",
    ),
    OperationDefinition(
        kind=OperationKind.METADATA_IMPORT,
        code="metadata-import",
        display_name="Metadata Import",
        description=(
            "Upload or reuse a metadata file, run a saved Import Metadata "
            "job, and optionally refresh the Planning cube."
        ),
        category="Application administration",
        risk_level="Elevated",
        route="/app/operations/metadata-import",
    ),
    OperationDefinition(
        kind=OperationKind.PIPELINE,
        code="pipelines",
        display_name="Pipelines",
        description=(
            "Run multi-stage Data Integration pipelines with live variables "
            "and stage-specific file requirements."
        ),
        category="Orchestration",
        risk_level="Elevated",
        route="/app/operations/pipelines",
    ),
    OperationDefinition(
        kind=OperationKind.DATA_INTEGRATION,
        code="data-integrations",
        display_name="Data Integrations",
        description=(
            "Load file-based data through configured Data Integration "
            "profiles, periods, and import/export modes."
        ),
        category="Data loading",
        risk_level="Elevated",
        route="/app/operations/data-integrations",
    ),
    OperationDefinition(
        kind=OperationKind.BUSINESS_RULE,
        code="business-rules",
        display_name="Business Rules",
        description=(
            "Run deployed Calculation Manager rules with optional runtime "
            "prompt values."
        ),
        category="Calculation",
        risk_level="Controlled",
        route="/app/operations/business-rules",
    ),
    OperationDefinition(
        kind=OperationKind.DATA_MAP,
        code="data-maps",
        display_name="Data Maps",
        description=(
            "Publish Planning data to a target cube with governed clear and "
            "member-override controls."
        ),
        category="Data movement",
        risk_level="Elevated",
        route="/app/operations/data-maps",
    ),
)


class PlanningProductProvider:
    """Expose the current Planning behavior through the product seam."""

    business_process = BusinessProcessType.PLANNING
    enabled = True

    def operations(self) -> tuple[OperationDefinition, ...]:
        return PLANNING_OPERATION_DEFINITIONS

    def navigation(self) -> tuple[NavigationDefinition, ...]:
        return PLANNING_NAVIGATION_DEFINITIONS

    def capabilities(self) -> tuple[CapabilityDefinition, ...]:
        operation_capabilities = tuple(
            CapabilityDefinition(
                code=item.code,
                scope=CapabilityScope.PRODUCT,
                description=item.description,
                agent_tools=(),
            )
            for item in PLANNING_OPERATION_DEFINITIONS
        )
        return PLANNING_CAPABILITIES + operation_capabilities

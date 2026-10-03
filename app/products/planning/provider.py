"""Planning operation contribution with backwards-compatible contracts."""

from __future__ import annotations

from app.products.contracts import (
    BusinessProcessType,
    OperationDefinition,
    OperationKind,
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

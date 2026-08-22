"""Standalone Oracle EPM operation discovery and execution use cases."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePath
from typing import Any

from app.application.substitution_variables import (
    SubstitutionVariableApplicationService,
    SubstitutionVariableOperationInput,
)
from app.application.user_variables import (
    UserVariableApplicationService,
    UserVariableOperationInput,
)
from app.application.reports import (
    ReportGenerationOperationInput,
    ReportWorkspaceService,
)
from app.clients.epm_client import EPMClient
from app.config.settings import Settings
from app.automation.epm_automate_runner import EPMAutomateRunner
from app.models.notification import (
    TaskNotificationEvent,
    TaskNotificationStatus,
)
from app.models.data_integration import (
    DataIntegrationFileReference,
    DataIntegrationPeriodRange,
)
from app.models.data_integration_catalog import DataIntegrationDefinition
from app.models.job import JobDefinition, JobRecordStatistics
from app.models.pipeline_catalog import PipelineCatalogDefinition
from app.models.oracle_artifact import (
    OracleArtifact,
    OracleArtifactSource,
    OracleArtifactStatus,
    OracleArtifactType,
    OracleEnvironment,
)
from app.models.pipeline_input import (
    PipelineFileRequirement,
    PipelineFileSelection,
    PipelineFileSource,
)
from app.models.workflow import WorkflowRun, WorkflowStepStatus
from app.models.access_control import ExecutionActor
from app.monitoring.job_monitor import JobMonitor
from app.services.business_rule_service import BusinessRuleService
from app.services.application_service import ApplicationService
from app.services.cube_refresh_service import CubeRefreshService
from app.services.data_integration_catalog_service import (
    DataIntegrationCatalogService,
)
from app.services.data_integration_service import DataIntegrationService
from app.services.data_service import DataService
from app.services.epm_automate_data_service import EPMAutomateDataService
from app.services.data_map_service import DataMapService
from app.services.file_service import FileService
from app.services.job_service import JobService
from app.services.metadata_service import MetadataService
from app.services.notification_service import create_notification_service
from app.services.pipeline_catalog_service import PipelineCatalogService
from app.services.oracle_artifact_registry import OracleArtifactRegistry
from app.services.pipeline_preflight_service import (
    PipelinePreflightService,
    build_pipeline_file_selection,
    local_upload_oracle_reference,
)
from app.services.pipeline_service import PipelineService
from app.services.user_variable_service import UserVariableService
from app.services.workflow_engine import WorkflowEngine, WorkflowStep
from app.services.workflow_repository import SQLWorkflowRepository
from app.utils.exceptions import AuthenticationError, EPMError, OperationError


class OperationKind(StrEnum):
    """Supported standalone operation identifiers."""

    BUSINESS_RULE = "BUSINESS_RULE"
    DATA_MAP = "DATA_MAP"
    PIPELINE = "PIPELINE"
    DATA_INTEGRATION = "DATA_INTEGRATION"
    METADATA_IMPORT = "METADATA_IMPORT"
    DATA_IMPORT = "DATA_IMPORT"
    SUBSTITUTION_VARIABLE = "SUBSTITUTION_VARIABLE"
    USER_VARIABLE = "USER_VARIABLE"
    CUBE_REFRESH = "CUBE_REFRESH"
    REPORT_GENERATION = "REPORT_GENERATION"
    STANDALONE_FLOW = "STANDALONE_FLOW"


@dataclass(frozen=True, slots=True)
class OperationDefinition:
    """Presentation and governance metadata for one operation."""

    kind: OperationKind
    code: str
    display_name: str
    description: str
    category: str
    risk_level: str
    route: str


@dataclass(frozen=True, slots=True)
class OperationCatalog:
    """Registered operations and any live Oracle artifacts currently available."""

    operations: tuple[OperationDefinition, ...]
    business_rules: tuple[str, ...]
    data_maps: tuple[str, ...]
    pipelines: tuple[PipelineCatalogDefinition, ...]
    data_integrations: tuple[DataIntegrationDefinition, ...]
    metadata_jobs: tuple[str, ...] = ()
    data_import_jobs: tuple[str, ...] = ()
    cube_refresh_jobs: tuple[str, ...] = ()
    oracle_available: bool = True
    oracle_message: str | None = None


@dataclass(frozen=True, slots=True)
class OracleArtifactSyncResult:
    """Outcome of one non-destructive Oracle catalog synchronization."""

    environment_key: str
    application_name: str
    oracle_available: bool
    verified_pipelines: int
    missing_pipelines: int
    discovered_integrations: int
    verified_integrations: int
    missing_integrations: int
    pending_integrations: int
    verification_errors: int
    message: str
    verified_business_rules: int = 0
    verified_data_maps: int = 0
    verified_metadata_jobs: int = 0
    verified_data_import_jobs: int = 0
    verified_cube_refresh_jobs: int = 0
    verified_cubes: int = 0
    total_verified: int = 0
    total_hidden: int = 0
    synchronized_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class BusinessRuleOperationInput:
    """Approved inputs for one Business Rule execution."""

    rule_name: str
    runtime_prompts: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class DataMapOperationInput:
    """Approved inputs for one Data Map execution."""

    data_map_name: str
    clear_target: bool
    member_overrides: Mapping[str, str]
    exclusion_overrides: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class PipelineOperationInput:
    """Approved runtime values and file choices for one Pipeline."""

    pipeline_code: str
    variables: Mapping[str, str]
    uploads: Mapping[str, Path]
    inbox_files: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class DataIntegrationOperationInput:
    """Approved file, period, and mode inputs for one integration."""

    integration_name: str
    start_period: str
    end_period: str
    import_mode: str
    export_mode: str
    upload_path: Path | None = None
    inbox_file: str | None = None
    use_configured_file: bool = False


@dataclass(frozen=True, slots=True)
class MetadataImportOperationInput:
    """Approved saved job, file source, and optional cube refresh."""

    job_name: str
    upload_path: Path | None = None
    inbox_file: str | None = None
    use_configured_file: bool = False
    error_file_name: str | None = None
    refresh_job_name: str | None = None


@dataclass(frozen=True, slots=True)
class DataImportOperationInput:
    """Approved saved native data job and file source."""

    job_name: str
    upload_path: Path | None = None
    inbox_file: str | None = None
    use_configured_file: bool = False
    error_file_name: str | None = None


@dataclass(frozen=True, slots=True)
class CubeRefreshOperationInput:
    """Approved saved Planning Cube Refresh job."""

    job_name: str


@dataclass(frozen=True, slots=True)
class PipelineVariablePreview:
    name: str
    display_name: str
    default_value: str | None
    required: bool
    editable: bool


@dataclass(frozen=True, slots=True)
class PipelineFilePreview:
    key: str
    display_name: str
    configured_reference: str | None
    required: bool
    allowed_extensions: tuple[str, ...]
    consumers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PipelineStagePreview:
    name: str
    display_name: str
    job_count: int
    runs_in_parallel: bool


@dataclass(frozen=True, slots=True)
class PipelineOperationPreview:
    code: str
    display_name: str
    variables: tuple[PipelineVariablePreview, ...]
    file_requirements: tuple[PipelineFilePreview, ...]
    stages: tuple[PipelineStagePreview, ...]


OperationInput = (
    BusinessRuleOperationInput
    | DataMapOperationInput
    | PipelineOperationInput
    | DataIntegrationOperationInput
    | MetadataImportOperationInput
    | DataImportOperationInput
    | SubstitutionVariableOperationInput
    | UserVariableOperationInput
    | CubeRefreshOperationInput
    | ReportGenerationOperationInput
)


OPERATION_DEFINITIONS = (
    OperationDefinition(
        kind=OperationKind.REPORT_GENERATION,
        code="report-generation",
        display_name="Report Generation",
        description=(
            "Export a registered report or Planning form POV and create a "
            "downloadable Excel workbook."
        ),
        category="Reporting",
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


class OperationCatalogService:
    """Discover selectable standalone Oracle artifacts."""

    _GENERIC_CUBE_REFRESH_NAMES = frozenset({"refreshcube"})

    _SUPPORTED_JOB_TYPES = frozenset(
        {
            "RULES",
            "PLAN_TYPE_MAP",
            "IMPORT_METADATA",
            "IMPORT_DATA",
            "CUBE_REFRESH",
        }
    )
    _JOB_ARTIFACT_TYPES = {
        "RULES": OracleArtifactType.BUSINESS_RULE,
        "PLAN_TYPE_MAP": OracleArtifactType.DATA_MAP,
        "IMPORT_METADATA": OracleArtifactType.METADATA_IMPORT_JOB,
        "IMPORT_DATA": OracleArtifactType.DATA_IMPORT_JOB,
        "CUBE_REFRESH": OracleArtifactType.CUBE_REFRESH_JOB,
    }

    def __init__(
        self,
        settings: Settings,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._settings = settings
        self._logger = logger or logging.getLogger(__name__)

    @staticmethod
    def definitions() -> tuple[OperationDefinition, ...]:
        """Return operations implemented by the current application build."""
        return OPERATION_DEFINITIONS

    def discover(self, *, include_live: bool = True) -> OperationCatalog:
        """Return registrations and optionally discover live Oracle artifacts.

        Pipeline and Data Integration registrations are stored locally and do
        not require an Oracle connection. Live discovery failures therefore
        degrade only the Business Rule and Data Map portions of the catalog.
        """
        registry = self._artifact_registry()
        pipelines = tuple(
            PipelineCatalogDefinition(
                code=item.oracle_identifier,
                name=item.display_name,
                description=item.description,
            )
            for item in registry.list(OracleArtifactType.PIPELINE)
            if item.is_verified
        )
        data_integrations = tuple(
            DataIntegrationDefinition(
                name=item.oracle_identifier,
                description=item.description,
            )
            for item in registry.list(OracleArtifactType.DATA_INTEGRATION)
            if item.is_verified
        )
        rules = ()
        maps = ()
        metadata_jobs = ()
        data_import_jobs = ()
        cube_refresh_jobs = ()
        oracle_available = True
        oracle_message = None
        if include_live:
            try:
                with EPMClient(
                    self._settings,
                    logger=self._logger.getChild("client"),
                ) as client:
                    client.authenticate()
                    jobs = JobService(
                        client,
                        logger=self._logger.getChild("job_service"),
                    )
                    rules = self._discover_job_definitions(
                        jobs,
                        job_type="RULES",
                    )
                    maps = self._discover_job_definitions(
                        jobs,
                        job_type="PLAN_TYPE_MAP",
                    )
                    metadata_jobs = self._discover_job_definitions(
                        jobs,
                        job_type="IMPORT_METADATA",
                    )
                    data_import_jobs = self._discover_job_definitions(
                        jobs,
                        job_type="IMPORT_DATA",
                    )
                    cube_refresh_jobs = self._discover_job_definitions(
                        jobs,
                        job_type="CUBE_REFRESH",
                    )
            except EPMError as exc:
                oracle_available = False
                oracle_message = (
                    "Oracle Planning is currently unavailable. Registered "
                    "Pipelines and Data Integrations are still available; "
                    "live inspection and execution require a connection."
                )
                self._logger.warning(
                    "Live Oracle operation discovery is unavailable: %s",
                    exc,
                )
        return OperationCatalog(
            operations=self.definitions(),
            business_rules=tuple(item.job_name for item in rules),
            data_maps=tuple(item.job_name for item in maps),
            pipelines=pipelines,
            data_integrations=data_integrations,
            metadata_jobs=tuple(
                item.job_name for item in metadata_jobs
            ),
            data_import_jobs=tuple(
                item.job_name for item in data_import_jobs
            ),
            cube_refresh_jobs=tuple(
                item.job_name for item in cube_refresh_jobs
            ),
            oracle_available=oracle_available,
            oracle_message=oracle_message,
        )

    def discover_registered(self) -> OperationCatalog:
        """Return local operation registrations without contacting Oracle."""
        return self.discover(include_live=False)

    def registered_artifacts(
        self,
        artifact_type: OracleArtifactType,
        *,
        include_inactive: bool = True,
    ) -> tuple[OracleArtifact, ...]:
        """Return lifecycle records for the connected Oracle application."""
        return self._artifact_registry().list(
            artifact_type,
            include_inactive=include_inactive,
        )

    def synchronized_catalog(
        self,
        *,
        include_inactive: bool = True,
    ) -> tuple[OracleArtifact, ...]:
        """Return the durable catalog for the connected Oracle environment."""
        return self._artifact_registry().list_all(
            include_inactive=include_inactive
        )

    def synchronize_artifacts(self) -> OracleArtifactSyncResult:
        """Synchronize every safely discoverable artifact in one session."""
        registry = self._artifact_registry()
        pipelines = registry.list(
            OracleArtifactType.PIPELINE,
            include_inactive=True,
        )
        verified = 0
        missing = 0
        errors = 0
        discovered: set[str] = set()
        job_counts = {
            artifact_type: 0
            for artifact_type in self._JOB_ARTIFACT_TYPES.values()
        }
        cube_count = 0
        try:
            with EPMClient(
                self._settings,
                logger=self._logger.getChild("catalog_sync_client"),
            ) as client:
                client.authenticate()
                jobs = JobService(
                    client,
                    logger=self._logger.getChild("catalog_sync_jobs"),
                )
                for job_type, artifact_type in self._JOB_ARTIFACT_TYPES.items():
                    try:
                        definitions = jobs.get_job_definitions(
                            job_type=job_type
                        )
                    except EPMError as exc:
                        errors += 1
                        self._logger.warning(
                            "Oracle catalog '%s' could not be synchronized: %s",
                            job_type,
                            exc,
                        )
                        continue
                    if job_type == "CUBE_REFRESH":
                        definitions = self._resolve_cube_refresh_definitions(
                            jobs,
                            definitions,
                        )
                    records = registry.reconcile_snapshot(
                        artifact_type,
                        (item.job_name for item in definitions),
                        description=f"Discovered from Oracle {job_type}",
                    )
                    job_counts[artifact_type] = sum(
                        item.is_verified for item in records
                    )

                try:
                    plan_types = ApplicationService(
                        client,
                        logger=self._logger.getChild("catalog_sync_application"),
                    ).get_plan_types()
                except EPMError as exc:
                    errors += 1
                    self._logger.warning(
                        "Oracle cube catalog could not be synchronized: %s",
                        exc,
                    )
                else:
                    cubes = registry.reconcile_snapshot(
                        OracleArtifactType.CUBE,
                        (item.name for item in plan_types),
                        description="Planning cube discovered from Oracle",
                    )
                    cube_count = sum(item.is_verified for item in cubes)

                service = PipelineService(
                    client,
                    logger=self._logger.getChild("catalog_sync_pipeline"),
                )
                for artifact in pipelines:
                    try:
                        details = service.get_pipeline_details(
                            artifact.oracle_identifier
                        )
                    except EPMError as exc:
                        if self._is_missing_artifact_error(exc):
                            registry.mark_missing(
                                OracleArtifactType.PIPELINE,
                                artifact.oracle_identifier,
                                str(exc),
                            )
                            missing += 1
                        else:
                            registry.record_verification_error(
                                OracleArtifactType.PIPELINE,
                                artifact.oracle_identifier,
                                str(exc),
                            )
                            errors += 1
                        continue
                    registry.mark_verified(
                        OracleArtifactType.PIPELINE,
                        artifact.oracle_identifier,
                        display_name=details.display_name,
                        description=artifact.description,
                    )
                    verified += 1
                    for integration_name in self._pipeline_integrations(details):
                        existing = registry.get(
                            OracleArtifactType.DATA_INTEGRATION,
                            integration_name,
                        )
                        registry.mark_verified(
                            OracleArtifactType.DATA_INTEGRATION,
                            integration_name,
                            display_name=integration_name,
                            description=(
                                existing.description
                                if existing is not None
                                else "Discovered from a verified Oracle Pipeline"
                            ),
                            source=(
                                None
                                if existing is not None
                                else OracleArtifactSource.PIPELINE_DISCOVERY
                            ),
                        )
                        discovered.add(integration_name.casefold())

                self._reconcile_data_integrations(
                    registry,
                    discovered,
                    pipeline_verification_errors=errors,
                )
        except EPMError as exc:
            self._logger.warning(
                "Oracle artifact synchronization unavailable: %s",
                exc,
            )
            return OracleArtifactSyncResult(
                environment_key=registry.environment.key,
                application_name=registry.environment.application_name,
                oracle_available=False,
                verified_pipelines=0,
                missing_pipelines=0,
                discovered_integrations=0,
                verified_integrations=0,
                missing_integrations=0,
                pending_integrations=0,
                verification_errors=len(pipelines),
                message=(
                    "Oracle could not be reached or authenticated. Existing "
                    "registrations were retained without status changes."
                ),
                synchronized_at=datetime.now(UTC),
            )
        integration_counts = self._integration_status_counts(registry)
        all_artifacts = registry.list_all(include_inactive=True)
        total_verified = sum(item.is_verified for item in all_artifacts)
        total_hidden = sum(
            item.status in {
                OracleArtifactStatus.MISSING,
                OracleArtifactStatus.INACTIVE,
            }
            for item in all_artifacts
        )
        synchronized_at = datetime.now(UTC)
        return OracleArtifactSyncResult(
            environment_key=registry.environment.key,
            application_name=registry.environment.application_name,
            oracle_available=True,
            verified_pipelines=verified,
            missing_pipelines=missing,
            discovered_integrations=len(discovered),
            verified_integrations=integration_counts[0],
            missing_integrations=integration_counts[1],
            pending_integrations=integration_counts[2],
            verification_errors=errors,
            verified_business_rules=job_counts[
                OracleArtifactType.BUSINESS_RULE
            ],
            verified_data_maps=job_counts[OracleArtifactType.DATA_MAP],
            verified_metadata_jobs=job_counts[
                OracleArtifactType.METADATA_IMPORT_JOB
            ],
            verified_data_import_jobs=job_counts[
                OracleArtifactType.DATA_IMPORT_JOB
            ],
            verified_cube_refresh_jobs=job_counts[
                OracleArtifactType.CUBE_REFRESH_JOB
            ],
            verified_cubes=cube_count,
            total_verified=total_verified,
            total_hidden=total_hidden,
            synchronized_at=synchronized_at,
            message=(
                f"Oracle catalog synchronized for "
                f"'{registry.environment.application_name}': "
                f"{total_verified} current artifact(s), {total_hidden} hidden "
                f"stale artifact(s), and {errors} catalog warning(s). "
                "Ambiguous connection or permission failures retain their "
                "prior state for administrator review."
            ),
        )

    def _reconcile_data_integrations(
        self,
        registry: OracleArtifactRegistry,
        discovered: set[str],
        *,
        pipeline_verification_errors: int,
    ) -> None:
        """Safely reconcile Integrations without executing a data load.

        Oracle exposes no standalone read-only Integration lookup.  Pipeline
        definitions, prior successful submissions, and explicit invalid-name
        responses are therefore the only authoritative non-mutating evidence.
        """
        integrations = registry.list(
            OracleArtifactType.DATA_INTEGRATION,
            include_inactive=True,
        )
        for artifact in integrations:
            if (
                artifact.last_error
                and artifact.status
                not in {OracleArtifactStatus.MISSING, OracleArtifactStatus.INACTIVE}
                and self._is_missing_artifact_error(
                    OperationError(artifact.last_error)
                )
            ):
                registry.mark_missing(
                    OracleArtifactType.DATA_INTEGRATION,
                    artifact.oracle_identifier,
                    artifact.last_error,
                )
                continue
            if (
                pipeline_verification_errors == 0
                and artifact.source == OracleArtifactSource.PIPELINE_DISCOVERY
                and artifact.is_verified
                and artifact.oracle_identifier.casefold() not in discovered
            ):
                registry.mark_missing(
                    OracleArtifactType.DATA_INTEGRATION,
                    artifact.oracle_identifier,
                    "The Integration is no longer referenced by any verified "
                    "Oracle Pipeline.",
                )

    @staticmethod
    def _integration_status_counts(
        registry: OracleArtifactRegistry,
    ) -> tuple[int, int, int]:
        artifacts = registry.list(
            OracleArtifactType.DATA_INTEGRATION,
            include_inactive=True,
        )
        verified = sum(item.is_verified for item in artifacts)
        missing = sum(
            item.status in {
                OracleArtifactStatus.MISSING,
                OracleArtifactStatus.INACTIVE,
            }
            for item in artifacts
        )
        pending = sum(
            item.is_active
            and item.status in {
                OracleArtifactStatus.PENDING,
                OracleArtifactStatus.UNAVAILABLE,
            }
            for item in artifacts
        )
        return verified, missing, pending

    def discover_job_names(self, *, job_type: str) -> tuple[str, ...]:
        """Retrieve names for one supported Oracle Planning job type."""
        normalized_type = self._normalize_job_type(job_type)
        return self.discover_job_names_for_types((normalized_type,))[
            normalized_type
        ]

    def discover_job_names_for_types(
        self,
        job_types: tuple[str, ...],
    ) -> dict[str, tuple[str, ...]]:
        """Retrieve multiple job catalogs through one authenticated session."""
        normalized_types = tuple(
            dict.fromkeys(self._normalize_job_type(item) for item in job_types)
        )
        if not normalized_types:
            return {}
        with EPMClient(
            self._settings,
            logger=self._logger.getChild("client"),
        ) as client:
            client.authenticate()
            jobs = JobService(
                client,
                logger=self._logger.getChild("job_service"),
            )
            registry = self._artifact_registry()
            result: dict[str, tuple[str, ...]] = {}
            for job_type in normalized_types:
                try:
                    definitions = jobs.get_job_definitions(job_type=job_type)
                except AuthenticationError as exc:
                    if exc.status_code != 403:
                        raise
                    self._logger.warning(
                        "The connected user cannot list '%s' job definitions; "
                        "the prior synchronized catalog is retained.",
                        job_type,
                    )
                    result[job_type] = ()
                    continue
                if job_type == "CUBE_REFRESH":
                    definitions = self._resolve_cube_refresh_definitions(
                        jobs,
                        definitions,
                    )
                names = tuple(item.job_name for item in definitions)
                registry.reconcile_snapshot(
                    self._JOB_ARTIFACT_TYPES[job_type],
                    names,
                    description=f"Discovered from Oracle {job_type}",
                )
                result[job_type] = names
            return result

    @classmethod
    def _normalize_job_type(cls, job_type: str) -> str:
        normalized_type = str(job_type).strip().upper()
        if normalized_type not in cls._SUPPORTED_JOB_TYPES:
            raise OperationError(
                f"Unsupported operation catalog job type '{job_type}'."
            )
        return normalized_type

    def _discover_job_definitions(
        self,
        jobs: JobService,
        *,
        job_type: str,
    ):
        try:
            definitions = jobs.get_job_definitions(job_type=job_type)
        except AuthenticationError as exc:
            if exc.status_code != 403:
                raise
            self._logger.warning(
                "The connected user cannot list '%s' job definitions; "
                "that operation catalog will be empty.",
                job_type,
            )
            return ()
        if job_type == "CUBE_REFRESH":
            return self._resolve_cube_refresh_definitions(jobs, definitions)
        return definitions

    def _resolve_cube_refresh_definitions(
        self,
        jobs: JobService,
        definitions: tuple[JobDefinition, ...],
    ) -> tuple[JobDefinition, ...]:
        """Return actual saved refresh jobs, never Oracle's generic label.

        Some Planning environments return only ``RefreshCube`` when the
        job-definition endpoint is filtered by ``CUBE_REFRESH``.  That value
        can be an API operation label rather than a saved Refresh Database job.
        Retry the complete definition catalog because affected environments
        may expose the real saved jobs there.  If they are still unavailable,
        return an empty catalog so the UI requests an exact saved name instead
        of presenting a known false suggestion.
        """
        saved = tuple(
            item
            for item in definitions
            if not self._is_generic_cube_refresh_name(item.job_name)
        )
        if saved:
            return saved

        try:
            all_definitions = jobs.get_job_definitions()
        except EPMError as exc:
            self._logger.warning(
                "Complete Oracle job discovery could not resolve saved Cube "
                "Refresh names: %s",
                exc,
            )
            return ()

        return tuple(
            item
            for item in all_definitions
            if self._normalize_definition_job_type(item.job_type)
            == "CUBE_REFRESH"
            and not self._is_generic_cube_refresh_name(item.job_name)
        )

    @classmethod
    def _is_generic_cube_refresh_name(cls, job_name: str) -> bool:
        return (
            str(job_name).strip().casefold()
            in cls._GENERIC_CUBE_REFRESH_NAMES
        )

    @staticmethod
    def _normalize_definition_job_type(job_type: str) -> str:
        return re.sub(r"[^A-Z0-9]+", "_", str(job_type).strip().upper()).strip(
            "_"
        )

    def preflight_pipeline(self, pipeline_code: str) -> PipelineOperationPreview:
        """Return live variables, stages, and file requirements."""
        definition = self._pipeline_definition(pipeline_code)
        try:
            preview = self._inspect_pipeline(definition.code)
        except EPMError as exc:
            if self._is_missing_artifact_error(exc):
                self._artifact_registry().mark_missing(
                    OracleArtifactType.PIPELINE,
                    definition.code,
                    str(exc),
                )
            raise
        self._artifact_registry().mark_verified(
            OracleArtifactType.PIPELINE,
            definition.code,
            display_name=preview.display_name,
        )
        return preview

    def register_pipeline(
        self,
        pipeline_code: str,
    ) -> PipelineOperationPreview:
        """Verify an exact Oracle code and persist it in the local catalog."""
        requested = PipelineService.validate_pipeline_code(pipeline_code)
        preview = self._inspect_pipeline(requested)
        self._artifact_registry().register(
            OracleArtifactType.PIPELINE,
            preview.code,
            display_name=preview.display_name,
            description="Verified from Oracle EPM",
            source=OracleArtifactSource.MANUAL,
            status=OracleArtifactStatus.VERIFIED,
        )
        return preview

    def register_data_integration(
        self,
        integration_name: str,
        *,
        description: str | None = None,
    ) -> OracleArtifact:
        """Register an exact name pending its first governed submission."""
        normalized = DataIntegrationService.validate_integration_name(
            integration_name
        )
        existing = self._artifact_registry().get(
            OracleArtifactType.DATA_INTEGRATION,
            normalized,
        )
        if existing is not None and existing.is_verified:
            return existing
        return self._artifact_registry().register(
            OracleArtifactType.DATA_INTEGRATION,
            normalized,
            display_name=normalized,
            description=description or "Awaiting first governed verification run",
            source=OracleArtifactSource.MANUAL,
            status=OracleArtifactStatus.PENDING,
        )

    def require_data_integration(self, integration_name: str) -> OracleArtifact:
        """Require an active verified or explicitly pending Integration."""
        return self._artifact_registry().require_runnable(
            OracleArtifactType.DATA_INTEGRATION,
            integration_name,
        )

    def _inspect_pipeline(
        self,
        pipeline_code: str,
    ) -> PipelineOperationPreview:
        """Retrieve a Pipeline directly from Oracle by exact code."""
        with EPMClient(
            self._settings,
            logger=self._logger.getChild("pipeline_client"),
        ) as client:
            client.authenticate()
            details = PipelineService(
                client,
                logger=self._logger.getChild("pipeline_service"),
            ).get_pipeline_details(pipeline_code)
        requirements = PipelinePreflightService(
            logger=self._logger.getChild("pipeline_preflight"),
        ).discover_file_requirements(details)
        file_variable_names = {
            requirement.variable_name.casefold()
            for requirement in requirements
            if requirement.variable_name
        }
        return PipelineOperationPreview(
            code=details.code,
            display_name=details.display_name,
            variables=tuple(
                PipelineVariablePreview(
                    name=variable.name,
                    display_name=variable.display_name,
                    default_value=variable.default_value,
                    required=variable.requires_value,
                    editable=True,
                )
                for variable in details.variables
                if variable.name.casefold() not in file_variable_names
            ),
            file_requirements=tuple(
                PipelineFilePreview(
                    key=requirement.key,
                    display_name=requirement.display_name,
                    configured_reference=(
                        requirement.configured_reference
                    ),
                    required=requirement.required,
                    allowed_extensions=tuple(
                        sorted(requirement.allowed_extensions)
                    ),
                    consumers=tuple(
                        consumer.display_label
                        for consumer in requirement.consumers
                    ),
                )
                for requirement in requirements
            ),
            stages=tuple(
                PipelineStagePreview(
                    name=stage.name,
                    display_name=stage.display_name,
                    job_count=len(stage.jobs),
                    runs_in_parallel=stage.runs_in_parallel,
                )
                for stage in details.stages
            ),
        )

    def _pipeline_definition(
        self,
        pipeline_code: str,
    ) -> PipelineCatalogDefinition:
        requested = PipelineService.validate_pipeline_code(pipeline_code)
        artifact = self._artifact_registry().require_runnable(
            OracleArtifactType.PIPELINE,
            requested,
        )
        return PipelineCatalogDefinition(
            code=artifact.oracle_identifier,
            name=artifact.display_name,
            description=artifact.description,
        )

    def _artifact_registry(self) -> OracleArtifactRegistry:
        environment = OracleEnvironment.from_settings(
            self._settings.epm_base_url,
            self._settings.application_name,
        )
        registry = OracleArtifactRegistry(
            self._settings.database_target,
            environment,
        )
        initial_seed_status = (
            OracleArtifactStatus.VERIFIED
            if not registry.has_any_registrations()
            else OracleArtifactStatus.PENDING
        )
        pipeline_seeds = PipelineCatalogService().load(
            self._settings.pipeline_catalog_file
        )
        registry.seed(
            OracleArtifactType.PIPELINE,
            (
                (item.code, item.name, item.description)
                for item in pipeline_seeds
            ),
            initial_status=initial_seed_status,
        )
        integration_seeds = DataIntegrationCatalogService().load(
            self._settings.data_integration_catalog_file
        )
        registry.seed(
            OracleArtifactType.DATA_INTEGRATION,
            (
                (item.name, item.name, item.description)
                for item in integration_seeds
            ),
            initial_status=initial_seed_status,
        )
        return registry

    @staticmethod
    def _pipeline_integrations(details) -> tuple[str, ...]:
        names: dict[str, str] = {}
        for stage in details.stages:
            for job in stage.jobs:
                job_type = " ".join(str(job.job_type or "").split()).casefold()
                if job_type not in {
                    "integration",
                    "data integration",
                    "data_integration",
                }:
                    continue
                names.setdefault(job.name.casefold(), job.name)
        return tuple(names.values())

    @staticmethod
    def _is_missing_artifact_error(exc: EPMError) -> bool:
        status_code = getattr(exc, "status_code", None)
        message = " ".join(str(exc).split()).casefold()
        if status_code == 404:
            return True
        return status_code in {None, 400} and any(
            phrase in message
            for phrase in (
                "not found",
                "unable to find",
                "no definition",
                "does not exist",
                "invalid job name",
                "invalid integration name",
                "invalid pipeline name",
                "pipeline name is invalid",
                "name is invalid",
            )
        )


class OperationCommandExecutor:
    """Execute one approved operation through the governed REST services."""

    def __init__(
        self,
        settings: Settings,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._settings = settings
        self._logger = logger or logging.getLogger(__name__)

    def execute(
        self,
        operation_input: OperationInput,
        *,
        execution_id: str,
        log_file: Path,
        actor: ExecutionActor | None = None,
    ) -> WorkflowRun:
        """Run, monitor, persist, log, and notify one standalone operation."""
        started_at = time.monotonic()
        log_file.parent.mkdir(parents=True, exist_ok=True)
        operation_kind, target_name = self._identity(operation_input)
        notification_service = create_notification_service(
            self._settings.email_notifications,
            logger=self._logger.getChild("notification_service"),
        )
        client: EPMClient | None = None
        state: dict[str, Any] = {}
        workflow_repository = SQLWorkflowRepository(
            self._settings.database_target
        )

        def write(message: str) -> None:
            normalized = " ".join(str(message).split())
            with log_file.open("a", encoding="utf-8") as stream:
                stream.write(normalized + "\n")

        def publish_running_evidence(
            details: Mapping[str, Any],
        ) -> None:
            """Persist safe Oracle submission evidence while polling."""
            run = workflow_repository.get(execution_id)
            if run is None:
                return
            steps = list(run.steps)
            for index, step in enumerate(steps):
                if step.status is not WorkflowStepStatus.RUNNING:
                    continue
                steps[index] = replace(
                    step,
                    details={**step.details, **dict(details)},
                )
                workflow_repository.save(
                    replace(run, steps=tuple(steps))
                )
                return

        def validate() -> Mapping[str, Any]:
            if isinstance(operation_input, BusinessRuleOperationInput):
                rule_name = BusinessRuleService.validate_rule_name(
                    operation_input.rule_name
                )
                prompts = BusinessRuleService.normalize_runtime_prompts(
                    operation_input.runtime_prompts
                )
                state["rule_name"] = rule_name
                state["runtime_prompts"] = dict(prompts)
                write(
                    f"Validated Business Rule '{rule_name}' with "
                    f"{len(prompts)} runtime prompt(s)."
                )
                return {
                    "operation": operation_kind.value,
                    "target_name": rule_name,
                    "runtime_prompt_names": [
                        name for name, _ in prompts
                    ],
                }
            if isinstance(operation_input, DataMapOperationInput):
                request = DataMapService.build_request(
                    operation_input.data_map_name,
                    clear_target=operation_input.clear_target,
                    member_overrides=operation_input.member_overrides,
                    exclusion_overrides=operation_input.exclusion_overrides,
                )
                state["data_map_request"] = request
                write(
                    f"Validated Data Map '{request.data_map_name}'; "
                    f"clear target={request.clear_target}."
                )
                return {
                    "operation": operation_kind.value,
                    "target_name": request.data_map_name,
                    "clear_target": request.clear_target,
                    "member_override_dimensions": [
                        name for name, _ in request.member_overrides
                    ],
                    "exclusion_dimensions": [
                        name for name, _ in request.exclusion_overrides
                    ],
                }
            if isinstance(operation_input, PipelineOperationInput):
                code = PipelineService.validate_pipeline_code(
                    operation_input.pipeline_code
                )
                supplied_variables = self._normalize_raw_variables(
                    operation_input.variables
                )
                self._validate_file_mapping_keys(
                    operation_input.uploads,
                    operation_input.inbox_files,
                )
                state["pipeline_code"] = code
                state["pipeline_variables"] = supplied_variables
                write(
                    f"Validated Pipeline '{code}' with "
                    f"{len(supplied_variables)} runtime value(s)."
                )
                return {
                    "operation": operation_kind.value,
                    "target_name": code,
                    "runtime_variable_names": list(
                        supplied_variables
                    ),
                    "upload_count": len(operation_input.uploads),
                    "inbox_file_count": len(
                        operation_input.inbox_files
                    ),
                }
            if isinstance(operation_input, MetadataImportOperationInput):
                job_name = str(operation_input.job_name).strip()
                if not job_name:
                    raise OperationError(
                        "Metadata Import job name cannot be empty."
                    )
                selected_sources = sum(
                    (
                        bool(operation_input.upload_path),
                        bool(operation_input.inbox_file),
                        operation_input.use_configured_file,
                    )
                )
                if selected_sources != 1:
                    raise OperationError(
                        "Metadata Import requires one local upload, existing "
                        "Oracle Inbox file, or Oracle configured file."
                    )
                if operation_input.upload_path is not None:
                    upload_path = Path(operation_input.upload_path)
                    if not upload_path.is_file():
                        raise OperationError(
                            "Metadata upload does not exist: "
                            f"'{upload_path}'."
                        )
                    file_name = upload_path.name
                    state["metadata_upload"] = upload_path
                    file_source = "local_upload"
                elif operation_input.inbox_file:
                    file_name = PurePath(
                        str(operation_input.inbox_file)
                    ).name.strip()
                    state["metadata_file"] = file_name
                    file_source = "existing_inbox"
                else:
                    file_name = None
                    state["metadata_file"] = None
                    file_source = "oracle_configured"
                if file_name is not None:
                    MetadataService.validate_inputs(file_name, job_name)
                error_file = (
                    PurePath(operation_input.error_file_name).name.strip()
                    if operation_input.error_file_name
                    else None
                )
                refresh_job = (
                    str(operation_input.refresh_job_name).strip()
                    if operation_input.refresh_job_name
                    else None
                )
                state["metadata_job"] = job_name
                state["metadata_error_file"] = error_file
                state["refresh_job"] = refresh_job
                write(
                    f"Validated Metadata Import '{job_name}' using "
                    f"'{file_name or 'the Oracle configured file'}'; "
                    f"refresh={bool(refresh_job)}."
                )
                return {
                    "operation": operation_kind.value,
                    "target_name": job_name,
                    "file_name": file_name,
                    "file_source": file_source,
                    "error_file_name": error_file,
                    "refresh_job_name": refresh_job,
                }
            if isinstance(operation_input, DataImportOperationInput):
                job_name = str(operation_input.job_name).strip()
                if not job_name:
                    raise OperationError(
                        "Planning Data Import job name cannot be empty."
                    )
                selected_sources = sum(
                    (
                        bool(operation_input.upload_path),
                        bool(operation_input.inbox_file),
                        operation_input.use_configured_file,
                    )
                )
                if selected_sources != 1:
                    raise OperationError(
                        "Planning Data Import requires one local upload, "
                        "existing Oracle Inbox file, or Oracle configured "
                        "file."
                    )
                if operation_input.upload_path is not None:
                    upload_path = Path(operation_input.upload_path)
                    if not upload_path.is_file():
                        raise OperationError(
                            "Data upload does not exist: "
                            f"'{upload_path}'."
                        )
                    file_name = upload_path.name
                    state["data_upload"] = upload_path
                    file_source = "local_upload"
                elif operation_input.inbox_file:
                    file_name = PurePath(
                        str(operation_input.inbox_file)
                    ).name.strip()
                    state["data_file"] = file_name
                    file_source = "existing_inbox"
                else:
                    file_name = None
                    state["data_configured_file"] = True
                    file_source = "oracle_configured"
                if file_name is not None:
                    DataService.validate_inputs(file_name, job_name)
                error_file = (
                    PurePath(operation_input.error_file_name).name.strip()
                    if operation_input.error_file_name
                    else None
                )
                state["data_import_job"] = job_name
                state["data_error_file"] = error_file
                write(
                    f"Validated Planning Data Import '{job_name}' using "
                    f"'{file_name or 'the Oracle configured file'}'."
                )
                return {
                    "operation": operation_kind.value,
                    "target_name": job_name,
                    "file_name": file_name,
                    "file_source": file_source,
                    "error_file_name": error_file,
                }
            if isinstance(
                operation_input,
                SubstitutionVariableOperationInput,
            ):
                command = (
                    SubstitutionVariableApplicationService.normalize_input(
                        operation_input
                    )
                )
                state["substitution_variable_command"] = command
                write(
                    f"Validated {command.action.value} for substitution "
                    f"variable '{command.scope}.{command.name}'."
                )
                return {
                    "operation": operation_kind.value,
                    "target_name": f"{command.scope}.{command.name}",
                    "action": command.action.value,
                    "scope": command.scope,
                    "name": command.name,
                    "new_value": command.value,
                }
            if isinstance(operation_input, UserVariableOperationInput):
                user_name, name, dimension, member = UserVariableService.validate_value(
                    operation_input.user_name,
                    operation_input.name,
                    operation_input.dimension,
                    operation_input.member,
                )
                command = UserVariableOperationInput(
                    user_name=user_name,
                    name=name,
                    dimension=dimension,
                    member=member,
                    expected_current_member=operation_input.expected_current_member,
                )
                state["user_variable_command"] = command
                write(
                    f"Validated user variable '{name}' for Oracle user '{user_name}'."
                )
                return {
                    "operation": operation_kind.value,
                    "target_name": f"{user_name}.{name}",
                    "user_name": user_name,
                    "name": name,
                    "dimension": dimension,
                    "new_member": member,
                }
            if isinstance(operation_input, CubeRefreshOperationInput):
                job_name = str(operation_input.job_name).strip()
                if not job_name:
                    raise OperationError(
                        "Cube Refresh job name cannot be empty."
                    )
                state["cube_refresh_job"] = job_name
                write(
                    f"Validated saved Cube Refresh job '{job_name}'."
                )
                return {
                    "operation": operation_kind.value,
                    "target_name": job_name,
                }
            if isinstance(operation_input, ReportGenerationOperationInput):
                command = ReportWorkspaceService.normalize_input(
                    operation_input
                )
                state["report_command"] = command
                write(
                    f"Validated report '{command.form_name}' with "
                    f"{len(command.page_member_overrides)} POV override(s)."
                )
                return {
                    "operation": operation_kind.value,
                    "target_name": command.form_name,
                    "title": command.title,
                    "pov_dimensions": [
                        dimension
                        for dimension, _ in (
                            command.page_member_overrides
                        )
                    ],
                }

            period_range = DataIntegrationPeriodRange.from_period_names(
                operation_input.start_period,
                operation_input.end_period,
            )
            import_mode = DataIntegrationService.normalize_import_mode(
                operation_input.import_mode
            )
            export_mode = DataIntegrationService.normalize_export_mode(
                operation_input.export_mode
            )
            selected_sources = sum(
                (
                    bool(operation_input.upload_path),
                    bool(operation_input.inbox_file),
                    operation_input.use_configured_file,
                )
            )
            if selected_sources != 1:
                raise OperationError(
                    "Data Integration requires one local upload, existing "
                    "Oracle Inbox reference, or Oracle configured file."
                )
            if operation_input.upload_path is not None:
                upload_path = Path(operation_input.upload_path)
                if not upload_path.is_file():
                    raise OperationError(
                        f"Data Integration upload does not exist: "
                        f"'{upload_path}'."
                    )
                state["integration_upload"] = upload_path
            elif operation_input.inbox_file:
                state["integration_file"] = str(
                    DataIntegrationFileReference.from_existing(
                        str(operation_input.inbox_file)
                    )
                )
            else:
                state["integration_file"] = None
            integration_name = str(
                operation_input.integration_name
            ).strip()
            if not integration_name:
                raise OperationError(
                    "Data Integration name cannot be empty."
                )
            state["integration_name"] = integration_name
            state["period_range"] = period_range
            state["import_mode"] = import_mode
            state["export_mode"] = export_mode
            write(
                f"Validated Data Integration '{integration_name}' for "
                f"{period_range.oracle_period_name}."
            )
            return {
                "operation": operation_kind.value,
                "target_name": integration_name,
                "period_range": period_range.oracle_period_name,
                "import_mode": import_mode,
                "export_mode": export_mode,
                "file_source": (
                    "local_upload"
                    if operation_input.upload_path is not None
                    else (
                        "existing_inbox"
                        if operation_input.inbox_file
                        else "oracle_configured"
                    )
                ),
            }

        def connect() -> Mapping[str, Any]:
            nonlocal client
            if (
                isinstance(operation_input, DataImportOperationInput)
                and operation_input.use_configured_file
            ):
                write(
                    "Prepared EPM Automate authentication for the saved "
                    "Planning Data Import job."
                )
                return {
                    "application": self._settings.application_name,
                    "engine": "epm_automate",
                }
            client = EPMClient(
                self._settings,
                logger=self._logger.getChild("client"),
            )
            client.authenticate()
            if not isinstance(
                operation_input,
                (
                    SubstitutionVariableOperationInput,
                    UserVariableOperationInput,
                    ReportGenerationOperationInput,
                ),
            ):
                state["job_service"] = JobService(
                    client,
                    logger=self._logger.getChild("job_service"),
                )
            write(
                f"Authenticated to Planning application "
                f"'{self._settings.application_name}'."
            )
            return {
                "application": self._settings.application_name,
                "engine": "rest",
            }

        def run_job() -> Mapping[str, Any]:
            if (
                isinstance(operation_input, DataImportOperationInput)
                and state.get("data_configured_file")
            ):
                runner = EPMAutomateRunner(
                    self._settings.epm_automate_executable,
                    timeout=self._settings.epm_automate_command_timeout,
                    logger=self._logger.getChild("epm_automate_runner"),
                )
                result = EPMAutomateDataService(
                    runner,
                    username=self._settings.epm_username,
                    password_file=(
                        self._settings.require_epm_automate_password_file()
                    ),
                    base_url=self._settings.epm_base_url,
                    logger=self._logger.getChild(
                        "epm_automate_data_service"
                    ),
                ).load_data(
                    data_file=None,
                    inbox_file_name=None,
                    job_name=state["data_import_job"],
                    error_file_name=state["data_error_file"],
                )
                write(
                    "Completed Planning Data Import using the file "
                    "configured in the saved Oracle job."
                )
                return {
                    "target_name": result.job_name,
                    "file_name": None,
                    "file_source": "oracle_configured",
                    "status": "Completed",
                    "engine": "epm_automate",
                }
            if client is None:
                raise OperationError(
                    "Oracle EPM connection was not initialized."
                )
            if isinstance(operation_input, ReportGenerationOperationInput):
                result = ReportWorkspaceService(
                    self._settings,
                    logger=self._logger.getChild("report_workspace"),
                ).generate(
                    client,
                    state["report_command"],
                    execution_id=execution_id,
                )
                write(
                    f"Generated '{result.output_path.name}' with "
                    f"{result.row_count} row(s) and "
                    f"{result.data_cell_count} data cell(s)."
                )
                return {
                    "target_name": result.form_name,
                    "output_file": result.output_path.name,
                    "row_count": result.row_count,
                    "data_cell_count": result.data_cell_count,
                    "pov": dict(result.pov),
                    "status": "Completed",
                    "engine": "rest",
                }
            if isinstance(
                operation_input,
                SubstitutionVariableOperationInput,
            ):
                result = SubstitutionVariableApplicationService(
                    client=client,
                    logger=self._logger.getChild(
                        "substitution_variables"
                    ),
                ).apply(state["substitution_variable_command"])
                write(
                    f"Oracle verified {result.action.value} for "
                    f"'{result.scope}.{result.name}'."
                )
                return {
                    "target_name": f"{result.scope}.{result.name}",
                    "action": result.action.value,
                    "old_value": result.old_value,
                    "new_value": result.new_value,
                    "changed": result.changed,
                    "status": "Completed",
                    "engine": "rest",
                }
            if isinstance(operation_input, UserVariableOperationInput):
                result = UserVariableApplicationService(
                    client=client,
                    logger=self._logger.getChild("user_variables"),
                ).apply(state["user_variable_command"])
                write(
                    f"Oracle verified user variable '{result.name}' for "
                    f"'{result.user_name}' as '{result.new_member}'."
                )
                return {
                    "target_name": f"{result.user_name}.{result.name}",
                    "user_name": result.user_name,
                    "name": result.name,
                    "dimension": result.dimension,
                    "old_member": result.old_member,
                    "new_member": result.new_member,
                    "changed": result.changed,
                    "status": "Completed",
                    "engine": "rest",
                }
            if isinstance(operation_input, CubeRefreshOperationInput):
                service = CubeRefreshService(
                    client,
                    logger=self._logger.getChild(
                        "cube_refresh_service"
                    ),
                )
                submission = service.start_refresh(
                    state["cube_refresh_job"]
                )
                monitor_service = service
                display_name = submission.job_name
            elif isinstance(operation_input, BusinessRuleOperationInput):
                service = BusinessRuleService(
                    client,
                    logger=self._logger.getChild("business_rule_service"),
                )
                submission = service.start_rule(
                    state["rule_name"],
                    runtime_prompts=state["runtime_prompts"],
                )
                monitor_service = state["job_service"]
                display_name = submission.rule_name
            elif isinstance(operation_input, DataMapOperationInput):
                request = state["data_map_request"]
                service = DataMapService(
                    client,
                    logger=self._logger.getChild("data_map_service"),
                )
                submission = service.start_data_map(
                    request.data_map_name,
                    clear_target=request.clear_target,
                    member_overrides=dict(request.member_overrides),
                    exclusion_overrides=dict(request.exclusion_overrides),
                )
                monitor_service = service
                display_name = request.data_map_name
            elif isinstance(operation_input, PipelineOperationInput):
                service = PipelineService(
                    client,
                    logger=self._logger.getChild("pipeline_service"),
                )
                details = service.get_pipeline_details(
                    state["pipeline_code"]
                )
                requirements = PipelinePreflightService(
                    logger=self._logger.getChild("pipeline_preflight"),
                ).discover_file_requirements(details)
                selections, variables = self._pipeline_execution_inputs(
                    details.variables,
                    requirements,
                    supplied_variables=state["pipeline_variables"],
                    uploads=operation_input.uploads,
                    inbox_files=operation_input.inbox_files,
                )
                PipelinePreflightService(
                    logger=self._logger.getChild("pipeline_files"),
                ).stage_uploads(client, selections)
                submission = service.start_pipeline(
                    state["pipeline_code"],
                    variables=variables,
                )
                monitor_service = service
                display_name = submission.pipeline_code
                write(
                    f"Prepared {len(selections)} Pipeline file input(s)."
                )
            elif isinstance(
                operation_input,
                MetadataImportOperationInput,
            ):
                if "metadata_upload" in state:
                    upload = FileService(
                        client,
                        supported_extensions={".csv", ".zip"},
                        logger=self._logger.getChild("file_service"),
                    ).upload_to_inbox(state["metadata_upload"])
                    state["metadata_file"] = upload.file_name
                    write(
                        f"Uploaded '{upload.file_name}' to Oracle Inbox; "
                        f"replaced existing={upload.replaced_existing}."
                    )
                service = MetadataService(
                    client,
                    logger=self._logger.getChild("metadata_service"),
                )
                submission = service.start_import(
                    state["metadata_file"],
                    state["metadata_job"],
                    error_file_name=state["metadata_error_file"],
                )
                monitor_service = state["job_service"]
                display_name = submission.job_name
            elif isinstance(operation_input, DataImportOperationInput):
                if "data_upload" in state:
                    upload = FileService(
                        client,
                        supported_extensions={
                            ".csv", ".txt", ".zip", ".dat"
                        },
                        logger=self._logger.getChild("file_service"),
                    ).upload_to_inbox(state["data_upload"])
                    state["data_file"] = upload.file_name
                    write(
                        f"Uploaded '{upload.file_name}' to Oracle Inbox; "
                        f"replaced existing={upload.replaced_existing}."
                    )
                service = DataService(
                    client,
                    logger=self._logger.getChild("data_service"),
                )
                submission = service.start_import(
                    state["data_file"],
                    state["data_import_job"],
                    error_file_name=state["data_error_file"],
                )
                monitor_service = state["job_service"]
                display_name = submission.job_name
            else:
                if "integration_upload" in state:
                    upload = FileService(
                        client,
                        supported_extensions={".csv", ".txt", ".zip"},
                        logger=self._logger.getChild("file_service"),
                    ).upload_to_inbox(state["integration_upload"])
                    state["integration_file"] = str(
                        DataIntegrationFileReference.from_default_upload(
                            upload.file_name
                        )
                    )
                    write(
                        f"Uploaded '{upload.file_name}' to Applications "
                        f"Inbox; replaced existing={upload.replaced_existing}."
                    )
                service = DataIntegrationService(
                    client,
                    logger=self._logger.getChild(
                        "data_integration_service"
                    ),
                )
                try:
                    submission = service.start_integration(
                        state["integration_file"],
                        state["integration_name"],
                        state["period_range"],
                        import_mode=state["import_mode"],
                        export_mode=state["export_mode"],
                    )
                except EPMError as exc:
                    registry = OracleArtifactRegistry(
                        self._settings.database_target,
                        OracleEnvironment.from_settings(
                            self._settings.epm_base_url,
                            self._settings.application_name,
                        ),
                    )
                    if OperationCatalogService._is_missing_artifact_error(exc):
                        registry.mark_missing(
                            OracleArtifactType.DATA_INTEGRATION,
                            state["integration_name"],
                            str(exc),
                        )
                    else:
                        registry.record_verification_error(
                            OracleArtifactType.DATA_INTEGRATION,
                            state["integration_name"],
                            str(exc),
                        )
                    raise
                OracleArtifactRegistry(
                    self._settings.database_target,
                    OracleEnvironment.from_settings(
                        self._settings.epm_base_url,
                        self._settings.application_name,
                    ),
                ).mark_verified(
                    OracleArtifactType.DATA_INTEGRATION,
                    submission.integration_name,
                    display_name=submission.integration_name,
                )
                monitor_service = service
                display_name = submission.integration_name
            write(
                f"Oracle job {submission.job_id} submitted for "
                f"'{display_name}'."
            )
            publish_running_evidence(
                {
                    "job_id": submission.job_id,
                    "target_name": display_name,
                    "status": "Submitted",
                    "engine": "rest",
                }
            )
            result = JobMonitor(
                monitor_service,
                poll_interval=self._settings.default_poll_interval,
                timeout=self._settings.default_job_timeout,
                logger=self._logger.getChild("job_monitor"),
            ).wait_for_completion(submission.job_id)
            write(
                f"Oracle job {result.job_id} completed successfully."
            )
            execution_details: dict[str, Any] = {
                "job_id": result.job_id,
                "target_name": display_name,
                "status": result.descriptive_status or result.status,
                "engine": "rest",
            }
            try:
                statistics = JobRecordStatistics.from_response(
                    result.raw_response
                )
            except EPMError as exc:
                self._logger.warning(
                    "Ignored invalid Oracle record counters for job %s: %s",
                    result.job_id,
                    exc,
                )
                statistics = None
            if (
                statistics is None
                and isinstance(
                    operation_input,
                    (MetadataImportOperationInput, DataImportOperationInput),
                )
            ):
                job_service = state.get("job_service")
                if job_service is not None:
                    try:
                        statistics = job_service.get_record_statistics(
                            result.job_id
                        )
                    except EPMError as exc:
                        self._logger.warning(
                            "Oracle Job Details statistics unavailable for "
                            "job %s: %s",
                            result.job_id,
                            exc,
                        )
                        write(
                            "Oracle completed the job, but this environment "
                            "did not expose record statistics."
                        )
            if statistics is not None:
                execution_details["record_statistics"] = (
                    statistics.to_payload()
                )
                write(
                    "Oracle load statistics: "
                    f"read={statistics.records_read}, "
                    f"processed={statistics.records_processed}, "
                    f"rejected={statistics.records_rejected}."
                )
            return execution_details

        def refresh_cube() -> Mapping[str, Any]:
            if client is None:
                raise OperationError(
                    "Oracle EPM connection was not initialized."
                )
            refresh_job = state.get("refresh_job")
            if not refresh_job:
                return {"skipped": True}
            service = CubeRefreshService(
                client,
                logger=self._logger.getChild("cube_refresh_service"),
            )
            submission = service.start_refresh(refresh_job)
            write(
                f"Cube Refresh job {submission.job_id} submitted for "
                f"'{submission.job_name}'."
            )
            publish_running_evidence(
                {
                    "job_id": submission.job_id,
                    "target_name": submission.job_name,
                    "status": "Submitted",
                    "engine": "rest",
                }
            )
            result = JobMonitor(
                service,
                poll_interval=self._settings.default_poll_interval,
                timeout=self._settings.default_job_timeout,
                logger=self._logger.getChild("cube_refresh_monitor"),
            ).wait_for_completion(submission.job_id)
            write(
                f"Cube Refresh job {result.job_id} completed successfully."
            )
            return {
                "job_id": result.job_id,
                "target_name": submission.job_name,
                "status": result.descriptive_status or result.status,
                "engine": "rest",
            }

        engine = WorkflowEngine(
            workflow_repository,
            logger=self._logger.getChild("workflow"),
        )
        workflow_name = f"{self._display_name(operation_kind)} · {target_name}"
        steps = [
            WorkflowStep("Validate operation inputs", validate),
            WorkflowStep("Connect to Oracle EPM", connect),
            WorkflowStep(
                f"Execute {self._display_name(operation_kind)}",
                run_job,
            ),
        ]
        if (
            isinstance(operation_input, MetadataImportOperationInput)
            and operation_input.refresh_job_name
        ):
            steps.append(
                WorkflowStep("Refresh Planning Cube", refresh_cube)
            )
        try:
            run = engine.run(
                workflow_name,
                tuple(steps),
                execution_id=execution_id,
                actor=actor,
            )
        except Exception as exc:
            notification_service.publish(
                self._notification(
                    operation_kind,
                    target_name,
                    TaskNotificationStatus.FAILED,
                    time.monotonic() - started_at,
                    error=exc,
                )
            )
            raise
        finally:
            if client is not None:
                client.close()

        notification_service.publish(
            self._notification(
                operation_kind,
                target_name,
                TaskNotificationStatus.SUCCESS,
                time.monotonic() - started_at,
            )
        )
        return run

    @staticmethod
    def _identity(
        operation_input: OperationInput,
    ) -> tuple[OperationKind, str]:
        if isinstance(operation_input, BusinessRuleOperationInput):
            return (
                OperationKind.BUSINESS_RULE,
                BusinessRuleService.validate_rule_name(
                    operation_input.rule_name
                ),
            )
        if isinstance(operation_input, DataMapOperationInput):
            request = DataMapService.build_request(
                operation_input.data_map_name,
                clear_target=operation_input.clear_target,
                member_overrides=operation_input.member_overrides,
                exclusion_overrides=operation_input.exclusion_overrides,
            )
            return OperationKind.DATA_MAP, request.data_map_name
        if isinstance(operation_input, PipelineOperationInput):
            return (
                OperationKind.PIPELINE,
                PipelineService.validate_pipeline_code(
                    operation_input.pipeline_code
                ),
            )
        if isinstance(operation_input, MetadataImportOperationInput):
            job_name = str(operation_input.job_name).strip()
            if not job_name:
                raise OperationError(
                    "Metadata Import job name cannot be empty."
                )
            return OperationKind.METADATA_IMPORT, job_name
        if isinstance(operation_input, DataImportOperationInput):
            job_name = str(operation_input.job_name).strip()
            if not job_name:
                raise OperationError(
                    "Planning Data Import job name cannot be empty."
                )
            return OperationKind.DATA_IMPORT, job_name
        if isinstance(
            operation_input,
            SubstitutionVariableOperationInput,
        ):
            command = SubstitutionVariableApplicationService.normalize_input(
                operation_input
            )
            return (
                OperationKind.SUBSTITUTION_VARIABLE,
                f"{command.scope}.{command.name}",
            )
        if isinstance(operation_input, UserVariableOperationInput):
            target = f"{operation_input.user_name.strip()}.{operation_input.name.strip()}"
            if target in {".", ""}:
                raise OperationError("User variable identity cannot be empty.")
            return OperationKind.USER_VARIABLE, target
        if isinstance(operation_input, CubeRefreshOperationInput):
            job_name = str(operation_input.job_name).strip()
            if not job_name:
                raise OperationError(
                    "Cube Refresh job name cannot be empty."
                )
            return OperationKind.CUBE_REFRESH, job_name
        if isinstance(operation_input, ReportGenerationOperationInput):
            command = ReportWorkspaceService.normalize_input(
                operation_input
            )
            return OperationKind.REPORT_GENERATION, command.form_name
        integration_name = str(
            operation_input.integration_name
        ).strip()
        if not integration_name:
            raise OperationError(
                "Data Integration name cannot be empty."
            )
        return OperationKind.DATA_INTEGRATION, integration_name

    @staticmethod
    def _display_name(kind: OperationKind) -> str:
        return {
            OperationKind.BUSINESS_RULE: "Business Rule",
            OperationKind.DATA_MAP: "Data Map",
            OperationKind.PIPELINE: "Pipeline",
            OperationKind.DATA_INTEGRATION: "Data Integration",
            OperationKind.METADATA_IMPORT: "Metadata Import",
            OperationKind.DATA_IMPORT: "Planning Data Import",
            OperationKind.SUBSTITUTION_VARIABLE: "Substitution Variable",
            OperationKind.USER_VARIABLE: "User Variable",
            OperationKind.CUBE_REFRESH: "Planning Cube Refresh",
            OperationKind.REPORT_GENERATION: "Report Generation",
        }[kind]

    @staticmethod
    def _validate_file_mapping_keys(
        uploads: Mapping[str, Path],
        inbox_files: Mapping[str, str],
    ) -> None:
        upload_keys = {
            str(key).strip().casefold()
            for key in uploads
            if str(key).strip()
        }
        inbox_keys = {
            str(key).strip().casefold()
            for key in inbox_files
            if str(key).strip()
        }
        duplicate = upload_keys & inbox_keys
        if duplicate:
            raise OperationError(
                "A Pipeline input cannot use both an upload and Inbox "
                "reference: " + ", ".join(sorted(duplicate))
            )

    @staticmethod
    def _normalize_raw_variables(
        variables: Mapping[str, str],
    ) -> dict[str, str]:
        normalized: dict[str, str] = {}
        seen: set[str] = set()
        for raw_name, raw_value in variables.items():
            name = str(raw_name).strip()
            value = str(raw_value).strip()
            if not name or not value:
                raise OperationError(
                    "Pipeline variable names and values cannot be empty."
                )
            key = name.casefold()
            if key in seen:
                raise OperationError(
                    f"Pipeline variable '{name}' was supplied more than once."
                )
            seen.add(key)
            normalized[name] = value
        return normalized

    @staticmethod
    def _pipeline_execution_inputs(
        variables,
        requirements: tuple[PipelineFileRequirement, ...],
        *,
        supplied_variables: Mapping[str, str],
        uploads: Mapping[str, Path],
        inbox_files: Mapping[str, str],
    ) -> tuple[tuple[PipelineFileSelection, ...], dict[str, str]]:
        upload_by_key = {
            str(key).casefold(): Path(value)
            for key, value in uploads.items()
        }
        inbox_by_key = {
            str(key).casefold(): str(value).strip()
            for key, value in inbox_files.items()
        }
        known_keys = {
            requirement.key.casefold()
            for requirement in requirements
        }
        unknown = (
            set(upload_by_key) | set(inbox_by_key)
        ) - known_keys
        if unknown:
            raise OperationError(
                "Pipeline file input(s) do not match the current definition: "
                + ", ".join(sorted(unknown))
            )

        selections: list[PipelineFileSelection] = []
        resolved_variables = {
            str(name): str(value)
            for name, value in supplied_variables.items()
        }
        resolved_by_name = {
            name.casefold(): name for name in resolved_variables
        }
        for requirement in requirements:
            key = requirement.key.casefold()
            if key in upload_by_key:
                path = upload_by_key[key]
                reference = local_upload_oracle_reference(
                    requirement,
                    path,
                )
                selection = build_pipeline_file_selection(
                    requirement,
                    source=PipelineFileSource.LOCAL_UPLOAD,
                    oracle_reference=reference,
                    local_path=path,
                )
            else:
                reference = (
                    inbox_by_key.get(key)
                    or requirement.configured_reference
                )
                if not reference:
                    if requirement.required:
                        raise OperationError(
                            f"Pipeline file input "
                            f"'{requirement.display_name}' is required."
                        )
                    continue
                selection = build_pipeline_file_selection(
                    requirement,
                    source=PipelineFileSource.EXISTING_INBOX,
                    oracle_reference=reference,
                )
            selections.append(selection)
            if requirement.variable_name:
                existing_name = resolved_by_name.get(
                    requirement.variable_name.casefold()
                )
                if existing_name:
                    resolved_variables.pop(existing_name)
                resolved_variables[
                    requirement.variable_name
                ] = selection.oracle_reference
                resolved_by_name[
                    requirement.variable_name.casefold()
                ] = requirement.variable_name

        for variable in variables:
            key = variable.name.casefold()
            existing_name = resolved_by_name.get(key)
            current = (
                resolved_variables.get(existing_name)
                if existing_name
                else None
            )
            if not current and variable.default_value:
                resolved_variables[variable.name] = variable.default_value
                resolved_by_name[key] = variable.name
                current = variable.default_value
            if variable.requires_value and not current:
                raise OperationError(
                    f"Pipeline variable '{variable.display_name}' requires "
                    "a value."
                )

        return tuple(selections), dict(
            PipelineService.normalize_variables(resolved_variables)
        )

    def _notification(
        self,
        kind: OperationKind,
        target_name: str,
        status: TaskNotificationStatus,
        duration_seconds: float,
        *,
        error: Exception | None = None,
    ) -> TaskNotificationEvent:
        return TaskNotificationEvent(
            task_name=f"{self._display_name(kind)} execution",
            status=status,
            environment_url=self._settings.epm_base_url,
            application_name=self._settings.application_name,
            execution_engine="rest",
            occurred_at=datetime.now().astimezone(),
            duration_seconds=max(duration_seconds, 0.0),
            job_or_integration_name=target_name,
            error_message=(
                " ".join(str(error).split())[:1_500]
                if error is not None
                else None
            ),
        )

"""Governed recovery planning for failed standalone Planning flows."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from app.application.execution_payloads import standalone_flow_from_payload
from app.application.operations import (
    BusinessRuleOperationInput,
    CubeRefreshOperationInput,
    DataImportOperationInput,
    DataIntegrationOperationInput,
    DataMapOperationInput,
    MetadataImportOperationInput,
    OperationCatalogService,
    PipelineOperationInput,
)
from app.application.standalone_flow import (
    StandaloneFlowInput,
    StandaloneFlowStepInput,
)
from app.config.settings import Settings
from app.models.execution_queue import ExecutionJobStatus, ExecutionJobType
from app.models.workflow import WorkflowStepStatus
from app.services.execution_queue_repository import SQLExecutionQueueRepository
from app.services.workflow_repository import SQLWorkflowRepository
from app.utils.exceptions import EPMError, OperationError


@dataclass(frozen=True, slots=True)
class RecoveryStepPreview:
    """One original operation selected for the recovery attempt."""

    sequence: int
    operation_code: str
    display_name: str
    artifact_name: str
    original_status: str


@dataclass(frozen=True, slots=True)
class RecoveryUploadRequirement:
    """A local source file that must be supplied again for recovery."""

    key: str
    step_sequence: int
    label: str
    original_filename: str
    allowed_extensions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StandaloneFlowRecoveryPlan:
    """Read-only, approval-ready recovery plan."""

    source_execution_id: str
    flow_name: str
    failed_step_sequence: int
    failure_reason: str
    retryable: bool
    blocked_reason: str | None
    steps: tuple[RecoveryStepPreview, ...]
    required_uploads: tuple[RecoveryUploadRequirement, ...]


class StandaloneFlowRecoveryService:
    """Rebuild a failed flow from durable evidence without replaying success."""

    def __init__(
        self,
        settings: Settings,
        *,
        catalog: OperationCatalogService | None = None,
    ) -> None:
        self._queue = SQLExecutionQueueRepository(settings.database_target)
        self._workflows = SQLWorkflowRepository(settings.database_target)
        self._catalog = catalog or OperationCatalogService(settings)

    def plan(self, source_execution_id: str) -> StandaloneFlowRecoveryPlan:
        """Return the exact failed-and-remaining sequence after validation."""
        execution_id = str(source_execution_id).strip()
        job = self._queue.get(execution_id)
        run = self._workflows.get(execution_id)
        if (
            job is None
            or job.job_type is not ExecutionJobType.STANDALONE_FLOW
            or run is None
        ):
            raise OperationError(
                "Only a retained standalone flow can be recovered."
            )
        if job.status is ExecutionJobStatus.RECOVERY_REQUIRED:
            return StandaloneFlowRecoveryPlan(
                source_execution_id=execution_id,
                flow_name=self._flow_name(run.workflow_name),
                failed_step_sequence=0,
                failure_reason=job.error_message or "Oracle outcome is unknown.",
                retryable=False,
                blocked_reason=(
                    "The worker outcome is uncertain. Review the Oracle Job "
                    "Console before creating a new execution."
                ),
                steps=(),
                required_uploads=(),
            )
        if job.status is not ExecutionJobStatus.FAILED:
            raise OperationError(
                "Recovery is available only after a standalone flow fails."
            )
        failed = next(
            (
                step
                for step in run.steps
                if step.status is WorkflowStepStatus.FAILED
            ),
            None,
        )
        if failed is None:
            raise OperationError(
                "The failed flow does not contain a recoverable failed step."
            )
        flow, _actor = standalone_flow_from_payload(job.payload)
        failed_index = failed.sequence - 1
        if failed_index < 0 or failed_index >= len(flow.steps):
            raise OperationError(
                "The retained flow sequence no longer matches its evidence."
            )
        remaining = flow.steps[failed_index:]
        blocked_reason: str | None = None
        try:
            self._validate_artifacts(remaining)
        except EPMError as exc:
            blocked_reason = str(exc)
        previews = tuple(
            RecoveryStepPreview(
                sequence=step.source_sequence or local_sequence,
                operation_code=step.operation_code,
                display_name=step.display_name,
                artifact_name=step.artifact_name,
                original_status=run.steps[local_sequence - 1].status.value,
            )
            for local_sequence, step in enumerate(
                remaining,
                start=failed.sequence,
            )
        )
        return StandaloneFlowRecoveryPlan(
            source_execution_id=execution_id,
            flow_name=self._flow_name(run.workflow_name),
            failed_step_sequence=failed.sequence,
            failure_reason=(
                failed.error_message
                or run.error_message
                or job.error_message
                or "The Oracle operation failed."
            ),
            retryable=blocked_reason is None,
            blocked_reason=blocked_reason,
            steps=previews,
            required_uploads=self._upload_requirements(
                remaining,
                start_sequence=failed.sequence,
            ),
        )

    def prepare_retry(
        self,
        source_execution_id: str,
        *,
        expected_failed_step: int,
        replacement_uploads: Mapping[str, Path],
    ) -> StandaloneFlowInput:
        """Revalidate and construct a new linked flow after explicit approval."""
        plan = self.plan(source_execution_id)
        if not plan.retryable:
            raise OperationError(
                plan.blocked_reason or "This flow cannot be retried safely."
            )
        if plan.failed_step_sequence != expected_failed_step:
            raise OperationError(
                "The recovery plan changed. Review the latest failed step "
                "before approving again."
            )
        supplied = {str(key).strip(): Path(path) for key, path in replacement_uploads.items()}
        expected = {item.key for item in plan.required_uploads}
        if set(supplied) != expected:
            missing = sorted(expected - set(supplied))
            unexpected = sorted(set(supplied) - expected)
            parts: list[str] = []
            if missing:
                parts.append("missing replacement(s): " + ", ".join(missing))
            if unexpected:
                parts.append("unexpected replacement(s): " + ", ".join(unexpected))
            raise OperationError(
                "Recovery files do not match the reviewed plan; "
                + "; ".join(parts)
            )
        for requirement in plan.required_uploads:
            path = supplied[requirement.key]
            if not path.is_file():
                raise OperationError(
                    f"Replacement file for '{requirement.label}' is unavailable."
                )
            if (
                requirement.allowed_extensions
                and path.suffix.casefold() not in requirement.allowed_extensions
            ):
                raise OperationError(
                    f"Replacement file for '{requirement.label}' must use: "
                    + ", ".join(requirement.allowed_extensions)
                )

        job = self._queue.get(plan.source_execution_id)
        if job is None:  # pragma: no cover - plan already asserted this.
            raise OperationError("The source execution is no longer available.")
        original, _actor = standalone_flow_from_payload(job.payload)
        remaining = original.steps[plan.failed_step_sequence - 1 :]
        recovered_steps = tuple(
            replace(
                step,
                source_sequence=(step.source_sequence or local_sequence),
                operation_input=self._replace_uploads(
                    step.operation_input,
                    step.source_sequence or local_sequence,
                    supplied,
                ),
            )
            for local_sequence, step in enumerate(
                remaining,
                start=plan.failed_step_sequence,
            )
        )
        return StandaloneFlowInput(
            name=f"Recovery - {plan.flow_name}",
            objective=(
                f"Retry execution {plan.source_execution_id} from original "
                f"step {recovered_steps[0].source_sequence}."
            ),
            steps=recovered_steps,
            recovery_source_execution_id=plan.source_execution_id,
            recovery_from_sequence=recovered_steps[0].source_sequence,
        )

    def _validate_artifacts(
        self,
        steps: tuple[StandaloneFlowStepInput, ...],
    ) -> None:
        job_types: dict[str, list[str]] = {}
        integrations: list[str] = []
        pipelines: list[str] = []
        for step in steps:
            value = step.operation_input
            if isinstance(value, BusinessRuleOperationInput):
                job_types.setdefault("RULES", []).append(value.rule_name)
            elif isinstance(value, DataMapOperationInput):
                job_types.setdefault("PLAN_TYPE_MAP", []).append(value.data_map_name)
            elif isinstance(value, MetadataImportOperationInput):
                job_types.setdefault("IMPORT_METADATA", []).append(value.job_name)
                if value.refresh_job_name:
                    job_types.setdefault("CUBE_REFRESH", []).append(
                        value.refresh_job_name
                    )
            elif isinstance(value, DataImportOperationInput):
                job_types.setdefault("IMPORT_DATA", []).append(value.job_name)
            elif isinstance(value, CubeRefreshOperationInput):
                job_types.setdefault("CUBE_REFRESH", []).append(value.job_name)
            elif isinstance(value, DataIntegrationOperationInput):
                integrations.append(value.integration_name)
            elif isinstance(value, PipelineOperationInput):
                pipelines.append(value.pipeline_code)

        if job_types:
            discovered = self._catalog.discover_job_names_for_types(
                tuple(job_types)
            )
            missing = [
                f"{job_type}: {name}"
                for job_type, names in job_types.items()
                for name in names
                if not self._contains(discovered.get(job_type, ()), name)
            ]
            if missing:
                raise OperationError(
                    "Oracle artifacts are no longer available: "
                    + ", ".join(missing)
                )
        for name in integrations:
            self._catalog.require_data_integration(name)
        for code in pipelines:
            self._catalog.preflight_pipeline(code)

    @staticmethod
    def _upload_requirements(
        steps: tuple[StandaloneFlowStepInput, ...],
        *,
        start_sequence: int,
    ) -> tuple[RecoveryUploadRequirement, ...]:
        result: list[RecoveryUploadRequirement] = []
        for local_sequence, step in enumerate(steps, start=start_sequence):
            sequence = step.source_sequence or local_sequence
            value = step.operation_input
            if isinstance(value, PipelineOperationInput):
                for upload_key, path in value.uploads.items():
                    result.append(
                        RecoveryUploadRequirement(
                            key=f"step_{sequence}:{upload_key}",
                            step_sequence=sequence,
                            label=f"{step.display_name} - {upload_key}",
                            original_filename=path.name,
                            allowed_extensions=(),
                        )
                    )
                continue
            if isinstance(value, DataIntegrationOperationInput):
                path = value.upload_path
                extensions = (".csv", ".txt", ".zip")
            elif isinstance(value, MetadataImportOperationInput):
                path = value.upload_path
                extensions = (".csv", ".zip")
            elif isinstance(value, DataImportOperationInput):
                path = value.upload_path
                extensions = (".csv", ".dat", ".txt", ".zip")
            else:
                path = None
                extensions = ()
            if path is not None:
                result.append(
                    RecoveryUploadRequirement(
                        key=f"step_{sequence}:source_file",
                        step_sequence=sequence,
                        label=f"{step.display_name} source file",
                        original_filename=path.name,
                        allowed_extensions=extensions,
                    )
                )
        return tuple(result)

    @staticmethod
    def _replace_uploads(
        operation_input,
        sequence: int,
        replacements: Mapping[str, Path],
    ):
        if isinstance(operation_input, PipelineOperationInput):
            return replace(
                operation_input,
                uploads={
                    key: replacements[f"step_{sequence}:{key}"]
                    for key in operation_input.uploads
                },
            )
        key = f"step_{sequence}:source_file"
        if isinstance(
            operation_input,
            (
                DataIntegrationOperationInput,
                MetadataImportOperationInput,
                DataImportOperationInput,
            ),
        ) and operation_input.upload_path is not None:
            return replace(operation_input, upload_path=replacements[key])
        return operation_input

    @staticmethod
    def _contains(values: tuple[str, ...], expected: str) -> bool:
        return any(value.casefold() == expected.casefold() for value in values)

    @staticmethod
    def _flow_name(workflow_name: str) -> str:
        prefix = "Standalone Flow - "
        return (
            workflow_name[len(prefix) :]
            if workflow_name.startswith(prefix)
            else workflow_name
        )

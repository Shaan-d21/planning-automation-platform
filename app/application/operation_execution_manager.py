"""Background manager for standalone Oracle EPM operations."""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from app.application.operations import (
    BusinessRuleOperationInput,
    CubeRefreshOperationInput,
    DataImportOperationInput,
    DataIntegrationOperationInput,
    DataMapOperationInput,
    MetadataImportOperationInput,
    OperationCommandExecutor,
    OperationInput,
    OperationKind,
    PipelineOperationInput,
)
from app.application.substitution_variables import (
    SubstitutionVariableApplicationService,
    SubstitutionVariableOperationInput,
)
from app.application.user_variables import UserVariableOperationInput
from app.application.reports import (
    ReportGenerationOperationInput,
    ReportWorkspaceService,
)
from app.config.settings import Settings
from app.application.execution_payloads import (
    operation_payload,
    standalone_flow_payload,
)
from app.application.execution_worker import DurableExecutionWorker
from app.application.standalone_flow import (
    StandaloneFlowInput,
    bind_standalone_flow_execution_id,
    standalone_flow_steps,
)
from app.models.execution_queue import (
    ExecutionJobStatus,
    ExecutionJobSubmission,
    ExecutionJobType,
)
from app.models.workflow import WorkflowRun, WorkflowStatus
from app.models.access_control import ExecutionActor, TriggerSource
from app.services.workflow_repository import SQLWorkflowRepository
from app.services.execution_queue_repository import SQLExecutionQueueRepository
from app.utils.exceptions import ExecutionQueueConflictError
from app.utils.exceptions import OperationError


class OperationExecutionStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


@dataclass(frozen=True, slots=True)
class ManagedOperationExecution:
    execution_id: str
    operation_kind: OperationKind
    target_name: str
    status: OperationExecutionStatus
    submitted_at: datetime
    log_file: Path
    error_message: str | None = None


class OperationExecutionManager:
    """Persist operation work before embedded or external execution."""

    def __init__(
        self,
        settings: Settings,
        *,
        max_workers: int = 3,
        logger: logging.Logger | None = None,
    ) -> None:
        self._settings = settings
        self._logger = logger or logging.getLogger(__name__)
        self._repository = SQLWorkflowRepository(
            settings.database_target
        )
        self._queue = SQLExecutionQueueRepository(settings.database_target)
        self._embedded = settings.execution_runtime == "embedded"
        self._worker = DurableExecutionWorker(
            settings,
            worker_id=f"embedded-operation-{uuid4().hex[:12]}",
            operation_executor_factory=OperationCommandExecutor,
            logger=self._logger.getChild("durable_worker"),
        )
        self._executor = (
            ThreadPoolExecutor(
                max_workers=max_workers,
                thread_name_prefix="epm-operation",
            )
            if self._embedded
            else None
        )
        self._executions: dict[str, ManagedOperationExecution] = {}
        self._futures: dict[str, Future[WorkflowRun]] = {}

    def submit(
        self,
        operation_input: OperationInput,
        *,
        cleanup: Callable[[], None] | None = None,
        actor: ExecutionActor | None = None,
        on_queued: Callable[[str], None] | None = None,
    ) -> ManagedOperationExecution:
        """Queue an operation and return its browser-visible identity."""
        kind, target = self._identity(operation_input)
        execution_id = str(uuid4())
        submitted_at = datetime.now(UTC)
        try:
            job = self._queue.enqueue(
                ExecutionJobSubmission(
                    execution_id=execution_id,
                    job_type=ExecutionJobType.OPERATION,
                    target_key=f"{kind.value}:{target}",
                    payload=operation_payload(operation_input, actor),
                ),
                now=submitted_at,
            )
        except ExecutionQueueConflictError as exc:
            raise OperationError(
                f"{self._display_name(kind)} '{target}' already has an "
                "active execution. " + str(exc)
            ) from exc
        try:
            if on_queued is not None:
                on_queued(execution_id)
            self._repository.save(
                WorkflowRun(
                    execution_id=execution_id,
                    workflow_name=f"{self._display_name(kind)} - {target}",
                    status=WorkflowStatus.QUEUED,
                    started_at=submitted_at,
                    initiated_by=actor.username if actor else None,
                    initiated_by_display=actor.display_name if actor else None,
                    trigger_source=(
                        actor.trigger_source if actor else TriggerSource.API
                    ),
                    oracle_execution_username=(
                        self._settings.oracle_execution_username
                    ),
                )
            )
        except Exception:
            self._queue.discard_queued(execution_id)
            raise
        execution = self._managed(job, kind=kind, target=target)
        if self._executor is not None:
            future = self._executor.submit(
                self._run_embedded,
                execution_id,
                cleanup,
            )
            self._futures[execution_id] = future
        return execution

    def get(
        self,
        execution_id: str,
    ) -> ManagedOperationExecution | None:
        job = self._queue.get(execution_id)
        if job is None or job.job_type not in {
            ExecutionJobType.OPERATION,
            ExecutionJobType.STANDALONE_FLOW,
        }:
            return None
        try:
            kind_name, target = job.target_key.split(":", 1)
            kind = OperationKind(kind_name)
        except (ValueError, TypeError):
            return None
        return self._managed(job, kind=kind, target=target)

    def submit_flow(
        self,
        flow_input: StandaloneFlowInput,
        *,
        cleanup: Callable[[], None] | None = None,
        actor: ExecutionActor | None = None,
    ) -> ManagedOperationExecution:
        """Queue one approved stop-on-failure standalone flow."""
        name = " ".join(str(flow_input.name).split()).strip()
        if not name or not flow_input.steps:
            raise OperationError(
                "A standalone flow requires a name and configured steps."
            )
        execution_id = str(uuid4())
        submitted_at = datetime.now(UTC)
        try:
            job = self._queue.enqueue(
                ExecutionJobSubmission(
                    execution_id=execution_id,
                    job_type=ExecutionJobType.STANDALONE_FLOW,
                    target_key=f"{OperationKind.STANDALONE_FLOW.value}:{name}",
                    payload=standalone_flow_payload(flow_input, actor),
                ),
                now=submitted_at,
            )
        except ExecutionQueueConflictError as exc:
            raise OperationError(
                f"Standalone Flow '{name}' already has an active execution. "
                + str(exc)
            ) from exc
        try:
            self._repository.save(
                WorkflowRun(
                    execution_id=execution_id,
                    workflow_name=f"Standalone Flow - {name}",
                    status=WorkflowStatus.QUEUED,
                    started_at=submitted_at,
                    steps=bind_standalone_flow_execution_id(
                        standalone_flow_steps(flow_input),
                        execution_id,
                    ),
                    initiated_by=actor.username if actor else None,
                    initiated_by_display=actor.display_name if actor else None,
                    trigger_source=(
                        actor.trigger_source if actor else TriggerSource.API
                    ),
                    oracle_execution_username=(
                        self._settings.oracle_execution_username
                    ),
                )
            )
        except Exception:
            self._queue.discard_queued(execution_id)
            raise
        execution = self._managed(
            job,
            kind=OperationKind.STANDALONE_FLOW,
            target=name,
        )
        if self._executor is not None:
            future = self._executor.submit(
                self._run_embedded,
                execution_id,
                cleanup,
            )
            self._futures[execution_id] = future
        return execution

    def get_workflow(self, execution_id: str) -> WorkflowRun | None:
        return self._repository.get(execution_id)

    def shutdown(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=False)

    def _run_embedded(
        self,
        execution_id: str,
        cleanup: Callable[[], None] | None,
    ) -> WorkflowRun | None:
        try:
            self._worker.run_once(execution_id, raise_failures=True)
            return self._repository.get(execution_id)
        finally:
            if cleanup is not None:
                try:
                    cleanup()
                except Exception:
                    self._logger.exception(
                        "Unable to clean temporary operation uploads for "
                        "execution '%s'.",
                        execution_id,
                    )

    def _managed(
        self,
        job,
        *,
        kind: OperationKind,
        target: str,
    ) -> ManagedOperationExecution:
        return ManagedOperationExecution(
            execution_id=job.execution_id,
            operation_kind=kind,
            target_name=target,
            status=OperationExecutionStatus(job.status.value),
            submitted_at=job.created_at,
            log_file=(
                self._settings.runtime_storage_dir
                / "operation_logs"
                / f"{job.execution_id}.log"
            ),
            error_message=job.error_message,
        )

    @staticmethod
    def _identity(
        operation_input: OperationInput,
    ) -> tuple[OperationKind, str]:
        if isinstance(operation_input, BusinessRuleOperationInput):
            target = operation_input.rule_name.strip()
            if not target:
                raise OperationError("Business Rule name cannot be empty.")
            return OperationKind.BUSINESS_RULE, target
        if isinstance(operation_input, DataMapOperationInput):
            target = operation_input.data_map_name.strip()
            if not target:
                raise OperationError("Data Map name cannot be empty.")
            return OperationKind.DATA_MAP, target
        if isinstance(operation_input, PipelineOperationInput):
            target = operation_input.pipeline_code.strip()
            if not target:
                raise OperationError("Pipeline code cannot be empty.")
            return OperationKind.PIPELINE, target
        if isinstance(operation_input, MetadataImportOperationInput):
            target = operation_input.job_name.strip()
            if not target:
                raise OperationError(
                    "Metadata Import job name cannot be empty."
                )
            return OperationKind.METADATA_IMPORT, target
        if isinstance(operation_input, DataImportOperationInput):
            target = operation_input.job_name.strip()
            if not target:
                raise OperationError(
                    "Planning Data Import job name cannot be empty."
                )
            return OperationKind.DATA_IMPORT, target
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
            user_name = operation_input.user_name.strip()
            name = operation_input.name.strip()
            if not user_name or not name:
                raise OperationError("Oracle user and user variable name are required.")
            return OperationKind.USER_VARIABLE, f"{user_name}.{name}"
        if isinstance(operation_input, CubeRefreshOperationInput):
            target = operation_input.job_name.strip()
            if not target:
                raise OperationError(
                    "Cube Refresh job name cannot be empty."
                )
            return OperationKind.CUBE_REFRESH, target
        if isinstance(operation_input, ReportGenerationOperationInput):
            command = ReportWorkspaceService.normalize_input(
                operation_input
            )
            return OperationKind.REPORT_GENERATION, command.form_name
        if isinstance(operation_input, DataIntegrationOperationInput):
            target = operation_input.integration_name.strip()
            if not target:
                raise OperationError(
                    "Data Integration name cannot be empty."
                )
            return OperationKind.DATA_INTEGRATION, target
        raise OperationError("Unsupported standalone operation input.")

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
            OperationKind.STANDALONE_FLOW: "Standalone Flow",
        }[kind]

"""Read models for the Oracle EPM web control center."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from app.config.settings import Settings
from app.models.workflow import WorkflowRun, WorkflowStatus
from app.services.planning_process_catalog_service import (
    PlanningProcessCatalogService,
)
from app.services.pipeline_process_repository import (
    SQLPipelineProcessRepository,
)
from app.services.workflow_repository import SQLWorkflowRepository


@dataclass(frozen=True, slots=True)
class ProcessView:
    """Presentation-safe summary of a configured Planning process."""

    code: str
    name: str
    cycle_code: str
    enabled_steps: int
    total_steps: int
    steps: tuple[str, ...]
    context_mode: str
    context_label: str
    preset_count: int


@dataclass(frozen=True, slots=True)
class RunView:
    """Presentation-safe workflow history item."""

    execution_id: str
    workflow_name: str
    status: str
    started_at: str
    duration: str
    completed_steps: int
    total_steps: int
    initiated_by: str
    trigger_source: str


@dataclass(frozen=True, slots=True)
class ArtifactView:
    """Downloadable report artifact shown in the UI."""

    name: str
    size: str
    modified_at: str


@dataclass(frozen=True, slots=True)
class ControlCenterSnapshot:
    """Complete read model for the first control-center viewport."""

    application_name: str
    environment_name: str
    environment_url: str
    deployment_mode: str
    processes: tuple[ProcessView, ...]
    recent_runs: tuple[RunView, ...]
    report_count: int
    success_rate: int
    successful_runs: int
    failed_runs: int
    running_runs: int


class ControlCenterService:
    """Assemble dashboard data without depending on a web framework."""

    def __init__(
        self,
        settings: Settings,
        *,
        process_catalog: PlanningProcessCatalogService | None = None,
        repository: SQLWorkflowRepository | None = None,
    ) -> None:
        self._settings = settings
        self._process_catalog = (
            process_catalog
            or PlanningProcessCatalogService(settings.database_target)
        )
        self._repository = repository or SQLWorkflowRepository(
            settings.database_target
        )
        self._process_designs = SQLPipelineProcessRepository(
            settings.database_target
        )

    def snapshot(self, *, history_limit: int = 8) -> ControlCenterSnapshot:
        """Return current catalog, history, and artifact metrics."""
        definitions = self._process_catalog.load(
            self._settings.planning_process_catalog_file
        )
        processes = tuple(
            ProcessView(
                code=definition.code,
                name=definition.display_name,
                cycle_code=definition.cycle_code,
                enabled_steps=sum(
                    step.enabled_by_default for step in definition.steps
                ),
                total_steps=len(definition.steps),
                steps=self._visible_process_steps(definition),
                context_mode=definition.context_mode.value,
                context_label=definition.context_mode.display_name,
                preset_count=len(
                    self._process_designs.list_profiles(definition.code)
                ),
            )
            for definition in definitions
        )
        runs = self._repository.list_recent(limit=history_limit)
        recent_runs = tuple(self._run_view(run) for run in runs)
        successful = sum(
            run.status is WorkflowStatus.SUCCESS for run in runs
        )
        failed = sum(run.status is WorkflowStatus.FAILED for run in runs)
        running = sum(
            run.status in {WorkflowStatus.QUEUED, WorkflowStatus.RUNNING}
            for run in runs
        )
        terminal = successful + failed
        success_rate = round(successful / terminal * 100) if terminal else 0
        parsed_url = urlparse(self._settings.epm_base_url)
        return ControlCenterSnapshot(
            application_name=self._settings.application_name,
            environment_name=parsed_url.hostname or "Oracle EPM",
            environment_url=self._settings.epm_base_url,
            deployment_mode=self._settings.resolved_deployment_mode,
            processes=processes,
            recent_runs=recent_runs,
            report_count=len(self.list_artifacts()),
            success_rate=success_rate,
            successful_runs=successful,
            failed_runs=failed,
            running_runs=running,
        )

    def list_artifacts(self) -> tuple[ArtifactView, ...]:
        """Return generated Excel reports in newest-first order."""
        directory = self._settings.report_output_dir
        if not directory.exists():
            return ()
        paths = sorted(
            (
                path
                for path in directory.iterdir()
                if path.is_file() and path.suffix.casefold() == ".xlsx"
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return tuple(self._artifact_view(path) for path in paths)

    def list_workflow_runs(self, *, limit: int = 100) -> tuple[WorkflowRun, ...]:
        """Return durable Oracle automation runs for the Jobs workspace."""
        return self._repository.list_recent(limit=limit)

    def get_workflow_run(self, execution_id: str) -> WorkflowRun | None:
        """Return one durable execution aggregate including its steps."""
        return self._repository.get(str(execution_id).strip())

    @staticmethod
    def _visible_process_steps(definition) -> tuple[str, ...]:
        """Expand captured Oracle Pipeline stages for business users."""
        visible: list[str] = []
        for step in definition.steps:
            if step.step_type.value == "PREFLIGHT":
                continue
            if step.step_type.value != "RUN_PIPELINE":
                visible.append(step.name)
                continue
            raw_stages = step.parameters.get("pipelineStages") or ()
            stage_names = tuple(
                str(item.get("displayName") or item.get("name") or "")
                .strip()
                for item in raw_stages
                if isinstance(item, dict)
            )
            visible.extend(name for name in stage_names if name)
            if not stage_names:
                visible.append("Oracle Pipeline")
        return tuple(visible)

    @staticmethod
    def _run_view(run: WorkflowRun) -> RunView:
        completed_steps = sum(
            step.status.value in {"SUCCESS", "FAILED", "SKIPPED"}
            for step in run.steps
        )
        return RunView(
            execution_id=run.execution_id,
            workflow_name=run.workflow_name,
            status=run.status.value,
            started_at=run.started_at.astimezone().strftime(
                "%d %b %Y, %I:%M %p"
            ),
            duration=ControlCenterService._duration(
                run.started_at,
                run.completed_at,
            ),
            completed_steps=completed_steps,
            total_steps=len(run.steps),
            initiated_by=(
                run.initiated_by_display or run.initiated_by or "System"
            ),
            trigger_source=run.trigger_source.value,
        )

    @staticmethod
    def _duration(
        started_at: datetime,
        completed_at: datetime | None,
    ) -> str:
        end = completed_at or datetime.now().astimezone()
        total_seconds = max(0, int((end - started_at).total_seconds()))
        minutes, seconds = divmod(total_seconds, 60)
        if minutes:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"

    @staticmethod
    def _artifact_view(path: Path) -> ArtifactView:
        stat = path.stat()
        size_kb = stat.st_size / 1024
        size = (
            f"{size_kb / 1024:.1f} MB"
            if size_kb >= 1024
            else f"{max(size_kb, 0.1):.1f} KB"
        )
        modified = datetime.fromtimestamp(
            stat.st_mtime
        ).astimezone().strftime("%d %b %Y, %I:%M %p")
        return ArtifactView(
            name=path.name,
            size=size,
            modified_at=modified,
        )

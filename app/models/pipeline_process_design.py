"""Versioned definitions created by the Oracle Pipeline Process Designer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.models.planning_cycle import PlanningCycleDefinition
from app.models.planning_process import (
    PlanningProcessDefinition,
    PlanningProcessStepType,
    ProcessContextMode,
)


class ProcessDesignStatus(StrEnum):
    """Lifecycle state for one saved process version."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class ProcessStepOwnership(StrEnum):
    """Recommended owner for one step in a Pipeline-backed Process."""

    PLATFORM_GATEWAY = "PLATFORM_GATEWAY"
    ORACLE_PIPELINE = "ORACLE_PIPELINE"
    PLATFORM_EXTENSION = "PLATFORM_EXTENSION"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"

    @property
    def display_name(self) -> str:
        """Return an administrator-facing ownership label."""
        return {
            self.PLATFORM_GATEWAY: "Keep in platform",
            self.ORACLE_PIPELINE: "Own in Oracle Pipeline",
            self.PLATFORM_EXTENSION: "Optional platform extension",
            self.REVIEW_REQUIRED: "Review before migration",
        }[self]


@dataclass(frozen=True, slots=True)
class PipelineProcessVersion:
    """One immutable version of a Pipeline-backed Planning process."""

    process: PlanningProcessDefinition
    cycle: PlanningCycleDefinition
    version: int
    status: ProcessDesignStatus
    created_at: datetime
    activated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PipelineProcessSummary:
    """Designer list item with current draft and activation information."""

    code: str
    display_name: str
    pipeline_code: str
    latest_version: int
    active_version: int | None
    latest_status: ProcessDesignStatus
    updated_at: datetime
    context_mode: ProcessContextMode


@dataclass(frozen=True, slots=True)
class PipelineRunProfile:
    """Reusable runtime values for one activated Pipeline process."""

    profile_id: int
    process_code: str
    name: str
    year: str
    start_period: str
    end_period: str
    scenario: str | None
    version: str | None
    pipeline_variables: tuple[tuple[str, str], ...]
    inbox_files: tuple[tuple[str, str], ...]
    required_upload_keys: tuple[str, ...]
    created_at: datetime

    @property
    def one_click_ready(self) -> bool:
        """Return whether execution needs no new local file."""
        return bool(self.year.strip()) and not self.required_upload_keys


@dataclass(frozen=True, slots=True)
class ProcessArchitectureFinding:
    """Ownership recommendation for one current Process step."""

    sequence: int
    step_type: PlanningProcessStepType
    step_name: str
    enabled: bool
    ownership: ProcessStepOwnership
    recommendation: str


@dataclass(frozen=True, slots=True)
class ProcessArchitectureReview:
    """Read-only migration assessment for one Pipeline-backed Process."""

    process_code: str
    process_name: str
    pipeline_code: str
    designer_managed: bool
    source_version: int | None
    pipeline_only: bool
    pipeline_stages: tuple[str, ...]
    runtime_variables: tuple[str, ...]
    findings: tuple[ProcessArchitectureFinding, ...]

    @property
    def aligned(self) -> bool:
        """Return whether the Process already has the thin target shape."""
        return self.pipeline_only and all(
            finding.step_type
            in {
                PlanningProcessStepType.PREFLIGHT,
                PlanningProcessStepType.RUN_PIPELINE,
            }
            for finding in self.findings
        )

    @property
    def requires_review(self) -> bool:
        """Return whether at least one legacy wrapper step remains."""
        return not self.aligned

    @property
    def pipeline_accepts_year(self) -> bool:
        """Return whether the saved Pipeline contract exposes YEAR."""
        return "YEAR" in {
            name.strip().upper() for name in self.runtime_variables
        }

    @property
    def can_prepare_migration(self) -> bool:
        """Return whether an offline thin draft can be prepared safely."""
        return self.requires_review and any(
            finding.step_type is PlanningProcessStepType.RUN_PIPELINE
            for finding in self.findings
        )

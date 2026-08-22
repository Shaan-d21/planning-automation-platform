"""Domain models for configurable end-to-end Planning processes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class PlanningProcessStepType(StrEnum):
    """Supported orchestration steps in execution order."""

    PREFLIGHT = "PREFLIGHT"
    UPDATE_VARIABLES = "UPDATE_VARIABLES"
    REFRESH_CUBE = "REFRESH_CUBE"
    RUN_PIPELINE = "RUN_PIPELINE"
    RUN_BUSINESS_RULE = "RUN_BUSINESS_RULE"
    RUN_DATA_MAP = "RUN_DATA_MAP"
    VALIDATE_DATA = "VALIDATE_DATA"
    GENERATE_REPORT = "GENERATE_REPORT"


class ProcessContextMode(StrEnum):
    """How a published process obtains its standard runtime context."""

    PROMPT_EACH_RUN = "PROMPT_EACH_RUN"
    PIPELINE_DEFAULTS = "PIPELINE_DEFAULTS"

    @property
    def display_name(self) -> str:
        """Return a concise administrator-facing label."""
        return {
            self.PROMPT_EACH_RUN: "Prompt at run",
            self.PIPELINE_DEFAULTS: "Use Oracle Pipeline defaults",
        }[self]


@dataclass(frozen=True, slots=True)
class PlanningProcessStepDefinition:
    """One configured step within an end-to-end Planning process."""

    step_type: PlanningProcessStepType
    name: str
    enabled_by_default: bool = True
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PlanningProcessDefinition:
    """Administrator-managed ordered Planning process."""

    code: str
    display_name: str
    cycle_code: str
    steps: tuple[PlanningProcessStepDefinition, ...]
    context_mode: ProcessContextMode = ProcessContextMode.PROMPT_EACH_RUN

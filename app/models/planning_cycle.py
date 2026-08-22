"""Domain models for reusable Planning cycle execution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re


class CycleValueRole(StrEnum):
    """Business values that may be mapped to substitution variables."""

    YEAR = "YEAR"
    START_PERIOD = "START_PERIOD"
    END_PERIOD = "END_PERIOD"
    SCENARIO = "SCENARIO"
    VERSION = "VERSION"

    @property
    def display_name(self) -> str:
        """Return a user-friendly role name."""
        return self.value.replace("_", " ").title()


@dataclass(frozen=True, slots=True)
class PlanningCycle:
    """Runtime values defining one planning cycle."""

    year: str
    start_period: str
    end_period: str
    scenario: str | None = None
    version: str | None = None

    def value_for(self, role: CycleValueRole) -> str | None:
        """Return the runtime value associated with a mapping role."""
        values = {
            CycleValueRole.YEAR: self.year,
            CycleValueRole.START_PERIOD: self._period_member(
                self.start_period
            ),
            CycleValueRole.END_PERIOD: self._period_member(self.end_period),
            CycleValueRole.SCENARIO: self.scenario,
            CycleValueRole.VERSION: self.version,
        }
        return values[role]

    @property
    def pipeline_start_period(self) -> str:
        """Return the start period in Data Integration runtime format."""
        return self._pipeline_period(self.start_period)

    @property
    def pipeline_end_period(self) -> str:
        """Return the end period in Data Integration runtime format."""
        return self._pipeline_period(self.end_period)

    def _pipeline_period(self, value: str) -> str:
        normalized = str(value).strip()
        if "#" in normalized or re.search(r"-\d{2,4}$", normalized):
            return normalized
        year = str(self.year).strip()
        year_suffix = year[-2:] if len(year) >= 2 else year
        return f"{normalized}-{year_suffix}"

    @staticmethod
    def _period_member(value: str) -> str:
        normalized = str(value).strip()
        if "#" in normalized:
            return normalized.split("#", 1)[0]
        if re.search(r"-\d{2,4}$", normalized):
            return normalized.rsplit("-", 1)[0]
        return normalized


@dataclass(frozen=True, slots=True)
class CycleVariableBinding:
    """Mapping between a cycle role and one scoped Oracle variable."""

    role: CycleValueRole
    variable_name: str
    scope: str


@dataclass(frozen=True, slots=True)
class PlanningCycleDefinition:
    """Persisted configuration for one approved planning workflow."""

    code: str
    display_name: str
    pipeline_code: str
    data_map_name: str | None
    variable_bindings: tuple[CycleVariableBinding, ...] = ()

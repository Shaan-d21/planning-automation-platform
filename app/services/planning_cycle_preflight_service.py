"""Pre-flight validation and value translation for Planning cycles."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.models.pipeline import PipelineDetails
from app.models.planning_cycle import PlanningCycle, PlanningCycleDefinition
from app.models.substitution_variable import (
    SubstitutionVariable,
    SubstitutionVariableUpdate,
)
from app.services.substitution_variable_service import (
    SubstitutionVariableService,
)
from app.utils.exceptions import PlanningCycleError


@dataclass(frozen=True, slots=True)
class PlanningCyclePreflightResult:
    """Validated values ready for an approved cycle execution."""

    updates: tuple[SubstitutionVariableUpdate, ...]
    pipeline_variables: tuple[tuple[str, str], ...]


class PlanningCyclePreflightService:
    """Validate configured artifacts before any Oracle state is changed."""

    def validate(
        self,
        definition: PlanningCycleDefinition,
        cycle: PlanningCycle,
        *,
        variables: Sequence[SubstitutionVariable],
        pipeline_details: PipelineDetails,
        available_data_maps: Sequence[str],
        variable_service: SubstitutionVariableService,
        require_cycle_values: bool = True,
    ) -> PlanningCyclePreflightResult:
        """Validate mappings and translate cycle values to runtime inputs."""
        if pipeline_details.code.casefold() != (
            definition.pipeline_code.casefold()
        ):
            raise PlanningCycleError(
                f"Pipeline '{definition.pipeline_code}' was not retrieved."
            )
        if (
            definition.data_map_name
            and definition.data_map_name.casefold()
            not in {name.casefold() for name in available_data_maps}
        ):
            raise PlanningCycleError(
                f"Data Map '{definition.data_map_name}' was not found."
            )
        if require_cycle_values and (
            not cycle.year or not cycle.start_period or not cycle.end_period
        ):
            raise PlanningCycleError(
                "Year, start period, and end period are required."
            )

        requested: dict[tuple[str, str], str] = {}
        for binding in definition.variable_bindings:
            value = cycle.value_for(binding.role)
            if not value:
                raise PlanningCycleError(
                    f"Cycle value '{binding.role.display_name}' is required "
                    f"by variable '{binding.scope}.{binding.variable_name}'."
                )
            requested[(binding.scope, binding.variable_name)] = value
        updates = variable_service.build_updates(
            requested,
            current_variables=variables,
        )

        live_names = {
            variable.name.upper(): variable
            for variable in pipeline_details.variables
        }
        runtime_values = {
            "YEAR": cycle.year or None,
            "STARTPERIOD": (
                cycle.pipeline_start_period if cycle.start_period else None
            ),
            "ENDPERIOD": (
                cycle.pipeline_end_period if cycle.end_period else None
            ),
            "SCENARIO": cycle.scenario,
            "VERSION": cycle.version,
        }
        pipeline_variables = tuple(
            (
                live_names[name].name,
                str(value),
            )
            for name, value in runtime_values.items()
            if name in live_names and value
        )
        return PlanningCyclePreflightResult(
            updates=updates,
            pipeline_variables=pipeline_variables,
        )

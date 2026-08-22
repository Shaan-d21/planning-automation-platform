"""JSON configuration for approved Planning cycle workflows."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app.infrastructure.database.engine import DatabaseTarget
from app.models.planning_cycle import (
    CycleValueRole,
    CycleVariableBinding,
    PlanningCycleDefinition,
)
from app.utils.exceptions import ConfigurationError


class PlanningCycleCatalogService:
    """Load built-in and published designer-managed cycle definitions."""

    def __init__(self, database_target: DatabaseTarget | None = None) -> None:
        self._database_target = database_target

    def load(self, path: Path) -> tuple[PlanningCycleDefinition, ...]:
        """Load all configured cycle definitions."""
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ConfigurationError(
                f"Planning cycle catalog does not exist: '{path}'."
            ) from exc
        except json.JSONDecodeError as exc:
            raise ConfigurationError(
                f"Planning cycle catalog contains invalid JSON: '{path}'."
            ) from exc
        if not isinstance(payload, Mapping):
            raise ConfigurationError(
                "Planning cycle catalog root must be a JSON object."
            )
        raw_cycles = payload.get("cycles")
        if not isinstance(raw_cycles, list):
            raise ConfigurationError(
                "Planning cycle catalog requires a cycles array."
            )
        definitions = tuple(
            self._definition(item)
            for item in raw_cycles
            if isinstance(item, Mapping)
        )
        codes = [item.code.casefold() for item in definitions]
        if len(codes) != len(set(codes)):
            raise ConfigurationError(
                "Planning cycle codes must be unique."
            )
        if self._database_target is None:
            return definitions
        from app.services.pipeline_process_repository import (
            SQLPipelineProcessRepository,
        )

        published = tuple(
            item.cycle
            for item in SQLPipelineProcessRepository(
                self._database_target
            ).list_active()
        )
        published_by_code = {
            item.code.casefold(): item for item in published
        }
        resolved = tuple(
            published_by_code.get(item.code.casefold(), item)
            for item in definitions
        )
        built_in_codes = {item.code.casefold() for item in definitions}
        return resolved + tuple(
            item
            for item in published
            if item.code.casefold() not in built_in_codes
        )

    def get(
        self,
        path: Path,
        code: str,
    ) -> PlanningCycleDefinition:
        """Return one cycle definition by case-insensitive code."""
        normalized = str(code).strip().casefold()
        for definition in self.load(path):
            if definition.code.casefold() == normalized:
                return definition
        raise ConfigurationError(
            f"Planning cycle '{code}' is not configured."
        )

    def save_bindings(
        self,
        path: Path,
        code: str,
        bindings: Sequence[CycleVariableBinding],
    ) -> PlanningCycleDefinition:
        """Replace only one definition's variable mappings atomically."""
        definitions = list(self.load(path))
        normalized = str(code).strip().casefold()
        index = next(
            (
                index
                for index, definition in enumerate(definitions)
                if definition.code.casefold() == normalized
            ),
            None,
        )
        if index is None:
            raise ConfigurationError(
                f"Planning cycle '{code}' is not configured."
            )
        current = definitions[index]
        updated = PlanningCycleDefinition(
            code=current.code,
            display_name=current.display_name,
            pipeline_code=current.pipeline_code,
            data_map_name=current.data_map_name,
            variable_bindings=tuple(bindings),
        )
        definitions[index] = updated
        payload = {
            "version": 1,
            "cycles": [
                self._serialize(definition)
                for definition in definitions
            ],
        }
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
        return updated

    @staticmethod
    def _definition(
        response: Mapping[str, Any],
    ) -> PlanningCycleDefinition:
        code = str(response.get("code", "")).strip()
        name = str(response.get("displayName", "")).strip() or code
        pipeline = str(response.get("pipelineCode", "")).strip()
        data_map = str(response.get("dataMapName", "")).strip() or None
        if not code or not pipeline:
            raise ConfigurationError(
                "Each planning cycle requires code and pipelineCode."
            )
        raw_bindings = response.get("variableBindings") or []
        if not isinstance(raw_bindings, list):
            raise ConfigurationError(
                f"Cycle '{code}' variableBindings must be an array."
            )
        bindings: list[CycleVariableBinding] = []
        for raw_binding in raw_bindings:
            if not isinstance(raw_binding, Mapping):
                raise ConfigurationError(
                    f"Cycle '{code}' contains an invalid variable binding."
                )
            try:
                role = CycleValueRole(
                    str(raw_binding.get("role", "")).strip().upper()
                )
            except ValueError as exc:
                raise ConfigurationError(
                    f"Cycle '{code}' contains an unsupported mapping role."
                ) from exc
            variable_name = str(
                raw_binding.get("variableName", "")
            ).strip()
            scope = str(raw_binding.get("scope", "")).strip()
            if not variable_name or not scope:
                raise ConfigurationError(
                    f"Cycle '{code}' binding requires variableName and "
                    "scope."
                )
            bindings.append(
                CycleVariableBinding(
                    role=role,
                    variable_name=variable_name,
                    scope=scope,
                )
            )
        binding_keys = [
            (
                binding.scope.casefold(),
                binding.variable_name.casefold(),
            )
            for binding in bindings
        ]
        if len(binding_keys) != len(set(binding_keys)):
            raise ConfigurationError(
                f"Cycle '{code}' maps the same scoped variable more than "
                "once."
            )
        return PlanningCycleDefinition(
            code=code,
            display_name=name,
            pipeline_code=pipeline,
            data_map_name=data_map,
            variable_bindings=tuple(bindings),
        )

    @staticmethod
    def _serialize(
        definition: PlanningCycleDefinition,
    ) -> dict[str, Any]:
        return {
            "code": definition.code,
            "displayName": definition.display_name,
            "pipelineCode": definition.pipeline_code,
            "dataMapName": definition.data_map_name,
            "variableBindings": [
                {
                    "role": binding.role.value,
                    "variableName": binding.variable_name,
                    "scope": binding.scope,
                }
                for binding in definition.variable_bindings
            ],
        }

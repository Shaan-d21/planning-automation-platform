"""Load administrator-managed end-to-end Planning processes."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.infrastructure.database.engine import DatabaseTarget
from app.models.planning_process import (
    PlanningProcessDefinition,
    PlanningProcessStepDefinition,
    PlanningProcessStepType,
    ProcessContextMode,
)
from app.utils.exceptions import ConfigurationError


class PlanningProcessCatalogService:
    """Read built-in and published designer-managed process definitions."""

    def __init__(self, database_target: DatabaseTarget | None = None) -> None:
        self._database_target = database_target

    def load(
        self,
        catalog_file: Path,
    ) -> tuple[PlanningProcessDefinition, ...]:
        """Return all configured processes in display order."""
        try:
            document = json.loads(catalog_file.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ConfigurationError(
                f"Planning process catalog does not exist: '{catalog_file}'."
            ) from exc
        except OSError as exc:
            raise ConfigurationError(
                f"Unable to read Planning process catalog "
                f"'{catalog_file}': {exc}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise ConfigurationError(
                "Planning process catalog contains invalid JSON at "
                f"line {exc.lineno}, column {exc.colno}: "
                f"'{catalog_file}'."
            ) from exc

        if not isinstance(document, Mapping):
            raise ConfigurationError(
                "Planning process catalog root must be a JSON object."
            )
        raw_processes = document.get("processes")
        if not isinstance(raw_processes, list):
            raise ConfigurationError(
                "Planning process catalog requires a 'processes' array."
            )
        definitions = tuple(
            self._definition(item, index)
            for index, item in enumerate(raw_processes, start=1)
            if isinstance(item, Mapping)
        )
        if len(definitions) != len(raw_processes):
            raise ConfigurationError(
                "Every Planning process entry must be a JSON object."
            )
        codes = [definition.code.casefold() for definition in definitions]
        if len(codes) != len(set(codes)):
            raise ConfigurationError(
                "Planning process codes must be unique."
            )
        if self._database_target is None:
            return definitions
        from app.services.pipeline_process_repository import (
            SQLPipelineProcessRepository,
        )

        published = tuple(
            item.process
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
        catalog_file: Path,
        code: str,
    ) -> PlanningProcessDefinition:
        """Return one process by case-insensitive code."""
        normalized = str(code).strip().casefold()
        for definition in self.load(catalog_file):
            if definition.code.casefold() == normalized:
                return definition
        raise ConfigurationError(
            f"Planning process '{code}' is not configured."
        )

    def _definition(
        self,
        response: Mapping[str, Any],
        index: int,
    ) -> PlanningProcessDefinition:
        code = self._required_text(response, "code", index)
        display_name = (
            str(response.get("displayName", "")).strip() or code
        )
        cycle_code = self._required_text(response, "cycleCode", index)
        raw_steps = response.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise ConfigurationError(
                f"Planning process '{code}' requires a non-empty steps array."
            )
        steps = tuple(
            self._step(item, code, step_index)
            for step_index, item in enumerate(raw_steps, start=1)
            if isinstance(item, Mapping)
        )
        if len(steps) != len(raw_steps):
            raise ConfigurationError(
                f"Every step in Planning process '{code}' must be an object."
            )
        names = [step.name.casefold() for step in steps]
        if len(names) != len(set(names)):
            raise ConfigurationError(
                f"Planning process '{code}' step names must be unique."
            )
        raw_context_mode = str(
            response.get(
                "contextMode",
                ProcessContextMode.PROMPT_EACH_RUN.value,
            )
        ).strip().upper()
        try:
            context_mode = ProcessContextMode(raw_context_mode)
        except ValueError as exc:
            raise ConfigurationError(
                f"Planning process '{code}' has unsupported contextMode "
                f"'{raw_context_mode}'."
            ) from exc
        return PlanningProcessDefinition(
            code=code,
            display_name=display_name,
            cycle_code=cycle_code,
            steps=steps,
            context_mode=context_mode,
        )

    def _step(
        self,
        response: Mapping[str, Any],
        process_code: str,
        index: int,
    ) -> PlanningProcessStepDefinition:
        raw_type = str(response.get("type", "")).strip().upper()
        try:
            step_type = PlanningProcessStepType(raw_type)
        except ValueError as exc:
            raise ConfigurationError(
                f"Planning process '{process_code}' step {index} has "
                f"unsupported type '{raw_type}'."
            ) from exc
        name = str(response.get("name", "")).strip()
        if not name:
            name = step_type.value.replace("_", " ").title()
        enabled = response.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ConfigurationError(
                f"Planning process '{process_code}' step '{name}' enabled "
                "must be true or false."
            )
        parameters = response.get("parameters") or {}
        if not isinstance(parameters, Mapping):
            raise ConfigurationError(
                f"Planning process '{process_code}' step '{name}' "
                "parameters must be an object."
            )
        return PlanningProcessStepDefinition(
            step_type=step_type,
            name=name,
            enabled_by_default=enabled,
            parameters=dict(parameters),
        )

    @staticmethod
    def _required_text(
        response: Mapping[str, Any],
        key: str,
        index: int,
    ) -> str:
        value = str(response.get(key, "")).strip()
        if not value:
            raise ConfigurationError(
                f"Planning process entry {index} requires '{key}'."
            )
        return value

"""Load the locally configured Oracle pipeline catalog."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.infrastructure.database.engine import DatabaseTarget
from app.models.pipeline_catalog import PipelineCatalogDefinition
from app.utils.exceptions import ConfigurationError


class PipelineCatalogService:
    """Read JSON seed entries and web-registered Pipeline definitions."""

    def __init__(self, database_target: DatabaseTarget | None = None) -> None:
        self._database_target = database_target

    def load(
        self,
        catalog_file: Path,
    ) -> tuple[PipelineCatalogDefinition, ...]:
        """Return validated pipelines in their configured display order."""
        try:
            raw_document = catalog_file.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise ConfigurationError(
                f"Pipeline catalog does not exist: '{catalog_file}'."
            ) from exc
        except OSError as exc:
            raise ConfigurationError(
                f"Unable to read the pipeline catalog '{catalog_file}': {exc}"
            ) from exc

        try:
            document = json.loads(raw_document)
        except json.JSONDecodeError as exc:
            raise ConfigurationError(
                "Pipeline catalog contains invalid JSON at "
                f"line {exc.lineno}, column {exc.colno}: '{catalog_file}'."
            ) from exc

        if not isinstance(document, dict):
            raise ConfigurationError(
                f"Pipeline catalog must contain a JSON object: "
                f"'{catalog_file}'."
            )
        entries = document.get("pipelines")
        if not isinstance(entries, list):
            raise ConfigurationError(
                "Pipeline catalog must contain a 'pipelines' array: "
                f"'{catalog_file}'."
            )

        definitions = tuple(
            self._parse_entry(entry, index, catalog_file)
            for index, entry in enumerate(entries, start=1)
        )
        self._validate_unique_codes(definitions, catalog_file)
        if self._database_target is None:
            return definitions
        from app.services.pipeline_registry import SQLPipelineRegistry

        registered = SQLPipelineRegistry(
            self._database_target
        ).list_all()
        known = {item.code.casefold() for item in definitions}
        return definitions + tuple(
            item for item in registered if item.code.casefold() not in known
        )

    @staticmethod
    def _parse_entry(
        entry: Any,
        index: int,
        catalog_file: Path,
    ) -> PipelineCatalogDefinition:
        if not isinstance(entry, dict):
            raise ConfigurationError(
                f"Pipeline entry {index} must be a JSON object in "
                f"'{catalog_file}'."
            )

        code = entry.get("code")
        name = entry.get("name")
        description = entry.get("description")
        if not isinstance(code, str) or not code.strip():
            raise ConfigurationError(
                f"Pipeline entry {index} requires a non-empty string "
                f"'code' in '{catalog_file}'."
            )
        if not isinstance(name, str) or not name.strip():
            raise ConfigurationError(
                f"Pipeline entry {index} requires a non-empty string "
                f"'name' in '{catalog_file}'."
            )
        if description is not None and not isinstance(description, str):
            raise ConfigurationError(
                f"Pipeline entry {index} has a non-string 'description' in "
                f"'{catalog_file}'."
            )
        return PipelineCatalogDefinition(
            code=code.strip(),
            name=name.strip(),
            description=(description.strip() or None) if description else None,
        )

    @staticmethod
    def _validate_unique_codes(
        definitions: tuple[PipelineCatalogDefinition, ...],
        catalog_file: Path,
    ) -> None:
        seen: set[str] = set()
        for definition in definitions:
            normalized_code = definition.code.casefold()
            if normalized_code in seen:
                raise ConfigurationError(
                    "Pipeline catalog contains a duplicate code "
                    f"'{definition.code}': '{catalog_file}'."
                )
            seen.add(normalized_code)

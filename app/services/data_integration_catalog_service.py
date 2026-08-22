"""Load the locally configured Oracle Data Integration catalog."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.models.data_integration_catalog import DataIntegrationDefinition
from app.utils.exceptions import ConfigurationError


class DataIntegrationCatalogService:
    """Read and validate Data Integration definitions from JSON."""

    def load(
        self,
        catalog_file: Path,
    ) -> tuple[DataIntegrationDefinition, ...]:
        """Return validated integrations in their configured display order."""
        try:
            raw_document = catalog_file.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise ConfigurationError(
                "Data Integration catalog does not exist: "
                f"'{catalog_file}'."
            ) from exc
        except OSError as exc:
            raise ConfigurationError(
                "Unable to read the Data Integration catalog "
                f"'{catalog_file}': {exc}"
            ) from exc

        try:
            document = json.loads(raw_document)
        except json.JSONDecodeError as exc:
            raise ConfigurationError(
                "Data Integration catalog contains invalid JSON at "
                f"line {exc.lineno}, column {exc.colno}: '{catalog_file}'."
            ) from exc

        entries = self._extract_entries(document, catalog_file)
        definitions = tuple(
            self._parse_entry(entry, index, catalog_file)
            for index, entry in enumerate(entries, start=1)
        )
        self._validate_unique_names(definitions, catalog_file)
        return definitions

    @staticmethod
    def _extract_entries(
        document: Any,
        catalog_file: Path,
    ) -> list[Any]:
        if not isinstance(document, dict):
            raise ConfigurationError(
                "Data Integration catalog must contain a JSON object: "
                f"'{catalog_file}'."
            )
        entries = document.get("integrations")
        if not isinstance(entries, list):
            raise ConfigurationError(
                "Data Integration catalog must contain an 'integrations' "
                f"array: '{catalog_file}'."
            )
        return entries

    @staticmethod
    def _parse_entry(
        entry: Any,
        index: int,
        catalog_file: Path,
    ) -> DataIntegrationDefinition:
        if not isinstance(entry, dict):
            raise ConfigurationError(
                f"Integration entry {index} must be a JSON object in "
                f"'{catalog_file}'."
            )

        raw_name = entry.get("name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise ConfigurationError(
                f"Integration entry {index} requires a non-empty string "
                f"'name' in '{catalog_file}'."
            )

        raw_description = entry.get("description")
        if raw_description is not None and not isinstance(
            raw_description,
            str,
        ):
            raise ConfigurationError(
                f"Integration entry {index} has a non-string 'description' "
                f"in '{catalog_file}'."
            )

        description = (
            raw_description.strip() if raw_description is not None else None
        )
        return DataIntegrationDefinition(
            name=raw_name.strip(),
            description=description or None,
        )

    @staticmethod
    def _validate_unique_names(
        definitions: tuple[DataIntegrationDefinition, ...],
        catalog_file: Path,
    ) -> None:
        seen: set[str] = set()
        for definition in definitions:
            normalized_name = definition.name.casefold()
            if normalized_name in seen:
                raise ConfigurationError(
                    "Data Integration catalog contains a duplicate name "
                    f"'{definition.name}': '{catalog_file}'."
                )
            seen.add(normalized_name)

"""Load administrator-managed data-slice report definitions."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from app.models.report import DataSliceReportDefinition, ReportAxisSegment
from app.utils.exceptions import ConfigurationError


class ReportCatalogService:
    """Read and validate reusable report definitions from JSON."""

    def load(
        self,
        catalog_file: Path,
    ) -> tuple[DataSliceReportDefinition, ...]:
        """Return all validated report definitions."""
        document = self._read_document(catalog_file)
        return self._definitions(document)

    def register(
        self,
        catalog_file: Path,
        definition: DataSliceReportDefinition,
        *,
        replace: bool = False,
    ) -> DataSliceReportDefinition:
        """Persist one validated report definition using an atomic write."""
        try:
            document = self._read_document(
                catalog_file,
                allow_missing=True,
            )
            raw_reports = document.setdefault("reports", [])
            if not isinstance(raw_reports, list):
                raise ConfigurationError(
                    "Report catalog requires a 'reports' array."
                )

            serialized = self._serialize(definition)
            normalized_name = definition.name.casefold()
            matching_indexes = [
                index
                for index, item in enumerate(raw_reports)
                if isinstance(item, Mapping)
                and str(item.get("name", "")).strip().casefold()
                == normalized_name
            ]
            if matching_indexes and not replace:
                raise ConfigurationError(
                    f"Report '{definition.name}' is already registered."
                )
            if matching_indexes:
                raw_reports[matching_indexes[0]] = serialized
            else:
                raw_reports.append(serialized)

            validated = self._definitions(document)
            registered = next(
                item
                for item in validated
                if item.name.casefold() == normalized_name
            )
            self._write_document(catalog_file, document)
            return registered
        except ConfigurationError:
            raise
        except OSError as exc:
            raise ConfigurationError(
                f"Unable to update report catalog '{catalog_file}': {exc}"
            ) from exc

    def _read_document(
        self,
        catalog_file: Path,
        *,
        allow_missing: bool = False,
    ) -> dict[str, Any]:
        try:
            document = json.loads(catalog_file.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            if allow_missing:
                return {"version": 1, "reports": []}
            raise ConfigurationError(
                f"Report catalog does not exist: '{catalog_file}'."
            ) from exc
        except OSError as exc:
            raise ConfigurationError(
                f"Unable to read report catalog '{catalog_file}': {exc}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise ConfigurationError(
                "Report catalog contains invalid JSON at "
                f"line {exc.lineno}, column {exc.colno}: "
                f"'{catalog_file}'."
            ) from exc

        if not isinstance(document, Mapping):
            raise ConfigurationError(
                "Report catalog root must be a JSON object."
            )
        return dict(document)

    def _definitions(
        self,
        document: Mapping[str, Any],
    ) -> tuple[DataSliceReportDefinition, ...]:
        raw_reports = document.get("reports")
        if not isinstance(raw_reports, list):
            raise ConfigurationError(
                "Report catalog requires a 'reports' array."
            )
        definitions = tuple(
            self._definition(item, index)
            for index, item in enumerate(raw_reports, start=1)
            if isinstance(item, Mapping)
        )
        if len(definitions) != len(raw_reports):
            raise ConfigurationError(
                "Every report catalog entry must be a JSON object."
            )
        names = [item.name.casefold() for item in definitions]
        if len(names) != len(set(names)):
            raise ConfigurationError(
                "Report catalog names must be unique."
            )
        return definitions

    @staticmethod
    def _serialize(
        definition: DataSliceReportDefinition,
    ) -> dict[str, Any]:
        return {
            "name": definition.name,
            "title": definition.title,
            "cube": definition.cube,
            "pov": {
                dimension: member
                for dimension, member in definition.pov
            },
            "columns": [
                {
                    "dimensions": list(segment.dimensions),
                    "members": [
                        list(selection)
                        for selection in segment.members
                    ],
                }
                for segment in definition.columns
            ],
            "rows": [
                {
                    "dimensions": list(segment.dimensions),
                    "members": [
                        list(selection)
                        for selection in segment.members
                    ],
                }
                for segment in definition.rows
            ],
        }

    @staticmethod
    def _write_document(
        catalog_file: Path,
        document: Mapping[str, Any],
    ) -> None:
        catalog_file.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                prefix=f".{catalog_file.name}.",
                suffix=".tmp",
                dir=catalog_file.parent,
                delete=False,
            ) as temporary:
                json.dump(
                    document,
                    temporary,
                    indent=2,
                    ensure_ascii=False,
                )
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            temporary_path.replace(catalog_file)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def delete(
        self,
        catalog_file: Path,
        name: str,
    ) -> DataSliceReportDefinition:
        """Remove one definition using the same atomic catalog write."""
        normalized = str(name).strip().casefold()
        if not normalized:
            raise ConfigurationError("Saved view name is required.")
        document = self._read_document(catalog_file)
        definitions = self._definitions(document)
        existing = next(
            (item for item in definitions if item.name.casefold() == normalized),
            None,
        )
        if existing is None:
            raise ConfigurationError(f"Saved view '{name}' was not found.")
        raw_reports = document.get("reports")
        assert isinstance(raw_reports, list)
        document["reports"] = [
            item
            for item in raw_reports
            if not (
                isinstance(item, Mapping)
                and str(item.get("name", "")).strip().casefold() == normalized
            )
        ]
        self._write_document(catalog_file, document)
        return existing

    def find(
        self,
        catalog_file: Path,
        name: str,
    ) -> DataSliceReportDefinition | None:
        """Return a report by case-insensitive name, if configured."""
        normalized = str(name).strip().casefold()
        return next(
            (
                definition
                for definition in self.load(catalog_file)
                if definition.name.casefold() == normalized
            ),
            None,
        )

    def _definition(
        self,
        response: Mapping[str, Any],
        index: int,
    ) -> DataSliceReportDefinition:
        name = self._required_text(response, "name", index)
        cube = self._required_text(response, "cube", index)
        title = str(response.get("title", "")).strip() or name
        pov = self._pov(response.get("pov"), name)
        columns = self._axis(response.get("columns"), name, "columns")
        rows = self._axis(response.get("rows"), name, "rows")

        self._validate_common_dimensions(rows, name, "row")
        self._validate_common_dimensions(columns, name, "column")
        dimensions = [
            *(dimension.casefold() for dimension, _ in pov),
            *(dimension.casefold() for dimension in rows[0].dimensions),
            *(dimension.casefold() for dimension in columns[0].dimensions),
        ]
        if len(dimensions) != len(set(dimensions)):
            raise ConfigurationError(
                f"Report '{name}' assigns a dimension to more than one axis."
            )
        return DataSliceReportDefinition(
            name=name,
            cube=cube,
            title=title,
            pov=pov,
            columns=columns,
            rows=rows,
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
                f"Report entry {index} requires a non-empty '{key}'."
            )
        return value

    @staticmethod
    def _pov(
        value: Any,
        report_name: str,
    ) -> tuple[tuple[str, str], ...]:
        if not isinstance(value, Mapping):
            raise ConfigurationError(
                f"Report '{report_name}' requires a POV object."
            )
        result = tuple(
            (str(dimension).strip(), str(member).strip())
            for dimension, member in value.items()
        )
        if any(not dimension or not member for dimension, member in result):
            raise ConfigurationError(
                f"Report '{report_name}' contains an empty POV value."
            )
        keys = [dimension.casefold() for dimension, _ in result]
        if len(keys) != len(set(keys)):
            raise ConfigurationError(
                f"Report '{report_name}' contains duplicate POV dimensions."
            )
        return result

    def _axis(
        self,
        value: Any,
        report_name: str,
        axis_name: str,
    ) -> tuple[ReportAxisSegment, ...]:
        if not self._is_sequence(value) or not value:
            raise ConfigurationError(
                f"Report '{report_name}' requires a non-empty "
                f"'{axis_name}' array."
            )
        segments: list[ReportAxisSegment] = []
        for raw_segment in value:
            if not isinstance(raw_segment, Mapping):
                raise ConfigurationError(
                    f"Report '{report_name}' contains an invalid "
                    f"{axis_name} segment."
                )
            raw_dimensions = raw_segment.get("dimensions")
            raw_members = raw_segment.get("members")
            if (
                not self._is_sequence(raw_dimensions)
                or not raw_dimensions
                or not self._is_sequence(raw_members)
                or len(raw_dimensions) != len(raw_members)
            ):
                raise ConfigurationError(
                    f"Report '{report_name}' {axis_name} dimensions and "
                    "member selections must be non-empty and aligned."
                )
            dimensions = tuple(
                str(dimension).strip() for dimension in raw_dimensions
            )
            members: list[tuple[str, ...]] = []
            for selection in raw_members:
                if not self._is_sequence(selection) or not selection:
                    raise ConfigurationError(
                        f"Report '{report_name}' has an empty member "
                        f"selection on its {axis_name} axis."
                    )
                normalized = tuple(
                    str(member).strip() for member in selection
                )
                if any(not member for member in normalized):
                    raise ConfigurationError(
                        f"Report '{report_name}' contains an empty member "
                        f"on its {axis_name} axis."
                    )
                members.append(normalized)
            if any(not dimension for dimension in dimensions):
                raise ConfigurationError(
                    f"Report '{report_name}' contains an empty dimension "
                    f"on its {axis_name} axis."
                )
            segments.append(
                ReportAxisSegment(
                    dimensions=dimensions,
                    members=tuple(members),
                )
            )
        return tuple(segments)

    @staticmethod
    def _validate_common_dimensions(
        segments: tuple[ReportAxisSegment, ...],
        report_name: str,
        axis_name: str,
    ) -> None:
        expected = segments[0].dimensions
        if any(segment.dimensions != expected for segment in segments[1:]):
            raise ConfigurationError(
                f"Report '{report_name}' must use the same dimensions in "
                f"every {axis_name} segment."
            )

    @staticmethod
    def _is_sequence(value: Any) -> bool:
        return isinstance(value, Sequence) and not isinstance(
            value,
            (str, bytes),
        )

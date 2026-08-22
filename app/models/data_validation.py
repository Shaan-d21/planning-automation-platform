"""Typed models for form-based source-to-target data validation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.utils.exceptions import DataValidationError


@dataclass(frozen=True, slots=True)
class FormLayout:
    """Planning form axes, page-member choices, and current POV."""

    page_dimensions: tuple[str, ...]
    row_dimensions: tuple[str, ...]
    column_dimensions: tuple[str, ...]
    allowed_page_members: tuple[tuple[str, tuple[str, ...]], ...] = ()
    pov: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_response(cls, response: Mapping[str, Any]) -> FormLayout:
        """Create a validated layout from a form structure response."""
        grid_info = response.get("gridInfo")
        if not isinstance(grid_info, Mapping):
            raise DataValidationError(
                "Form layout response did not contain gridInfo."
            )
        raw_allowed = grid_info.get("allowedPageMembersByDim") or {}
        if not isinstance(raw_allowed, Mapping):
            raise DataValidationError(
                "Form layout returned invalid allowed page members."
            )
        allowed: list[tuple[str, tuple[str, ...]]] = []
        for dimension, members in raw_allowed.items():
            if not _is_sequence(members):
                raise DataValidationError(
                    "Form layout returned invalid allowed page members."
                )
            allowed.append(
                (
                    str(dimension),
                    tuple(str(member) for member in members),
                )
            )
        return cls(
            page_dimensions=_text_tuple(
                grid_info.get("pageDimNames")
            ),
            row_dimensions=_text_tuple(grid_info.get("rowDimNames")),
            column_dimensions=_text_tuple(
                grid_info.get("columnDimNames")
            ),
            allowed_page_members=tuple(allowed),
            pov=_pov_tuple(response.get("pov")),
        )


@dataclass(frozen=True, slots=True)
class FormGridRow:
    """One row of member headers and aligned data values."""

    headers: tuple[str, ...]
    data: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class FormGrid:
    """Normalized JSON grid exported from an Oracle Planning form."""

    row_dimensions: tuple[str, ...]
    column_dimensions: tuple[str, ...]
    columns: tuple[tuple[str, ...], ...]
    rows: tuple[FormGridRow, ...]
    pov: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_response(cls, response: Mapping[str, Any]) -> FormGrid:
        """Create a validated grid from the Export Form Data response."""
        grid_info = response.get("gridInfo")
        raw_rows = response.get("rows")
        raw_columns = response.get("columns")
        if not isinstance(grid_info, Mapping):
            raise DataValidationError(
                "Form export response did not contain gridInfo."
            )
        if not _is_sequence(raw_rows) or not _is_sequence(raw_columns):
            raise DataValidationError(
                "Form export response did not contain rows and columns."
            )

        columns = tuple(
            tuple(str(value) for value in column)
            for column in raw_columns
            if _is_sequence(column)
        )
        if len(columns) != len(raw_columns):
            raise DataValidationError(
                "Form export returned an invalid column definition."
            )

        rows: list[FormGridRow] = []
        for raw_row in raw_rows:
            if not isinstance(raw_row, Mapping):
                raise DataValidationError(
                    "Form export returned an invalid row."
                )
            headers = raw_row.get("headers")
            data = raw_row.get("data")
            if not _is_sequence(headers) or not _is_sequence(data):
                raise DataValidationError(
                    "Form export row did not contain headers and data."
                )
            if len(data) != len(columns):
                raise DataValidationError(
                    "Form export row data does not align with its columns."
                )
            rows.append(
                FormGridRow(
                    headers=tuple(str(value) for value in headers),
                    data=tuple(data),
                )
            )

        return cls(
            row_dimensions=_text_tuple(grid_info.get("rowDimNames")),
            column_dimensions=_text_tuple(
                grid_info.get("columnDimNames")
            ),
            columns=columns,
            rows=tuple(rows),
            pov=_pov_tuple(response.get("pov")),
        )


@dataclass(frozen=True, slots=True)
class DataMismatch:
    """One source and target cell that failed reconciliation."""

    row_headers: tuple[str, ...]
    column_headers: tuple[str, ...]
    source_value: Decimal | None
    target_value: Decimal | None
    difference: Decimal | None


@dataclass(frozen=True, slots=True)
class DataComparisonCell:
    """One compared source and target cell, including successful matches."""

    row_headers: tuple[str, ...]
    column_headers: tuple[str, ...]
    source_value: Decimal | None
    target_value: Decimal | None
    difference: Decimal | None
    matches: bool


@dataclass(frozen=True, slots=True)
class DataValidationResult:
    """Summary of a source-to-target form reconciliation."""

    source_form: str
    target_form: str
    compared_cells: int
    matched_cells: int
    mismatches: tuple[DataMismatch, ...]
    tolerance: Decimal
    cells: tuple[DataComparisonCell, ...] = ()

    @property
    def is_successful(self) -> bool:
        """Return whether every compared cell matched."""
        return not self.mismatches


@dataclass(frozen=True, slots=True)
class DataQualityRules:
    """Read-only business checks applied to one Planning grid."""

    check_missing: bool = True
    check_zero: bool = False
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    max_issues: int = 500


@dataclass(frozen=True, slots=True)
class DataQualityIssue:
    """One cell that failed a configured data-quality check."""

    code: str
    severity: str
    message: str
    pov: tuple[tuple[str, str], ...]
    row_headers: tuple[str, ...]
    column_headers: tuple[str, ...]
    raw_value: Any
    numeric_value: Decimal | None


@dataclass(frozen=True, slots=True)
class DataQualityResult:
    """Summary and bounded exception evidence for one Planning grid."""

    status: str
    checked_cells: int
    passed_cells: int
    issue_count: int
    missing_count: int
    zero_count: int
    below_minimum_count: int
    above_maximum_count: int
    non_numeric_count: int
    issues: tuple[DataQualityIssue, ...]
    truncated: bool
    rules: DataQualityRules


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes),
    )


def _text_tuple(value: Any) -> tuple[str, ...]:
    if not _is_sequence(value):
        return ()
    return tuple(str(item) for item in value)


def _pov_tuple(value: Any) -> tuple[tuple[str, str], ...]:
    raw_pov = value or {}
    if not isinstance(raw_pov, Mapping):
        raise DataValidationError(
            "Form response returned an invalid POV."
        )
    return tuple(
        (str(key), str(member))
        for key, member in raw_pov.items()
    )

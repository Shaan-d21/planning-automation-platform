"""Typed models for Planning form-based reports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ReportAxisSegment:
    """One rectangular row or column selection in a data-slice report."""

    dimensions: tuple[str, ...]
    members: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class DataSliceReportDefinition:
    """Administrator-managed report definition for Export Data Slice."""

    name: str
    cube: str
    title: str
    pov: tuple[tuple[str, str], ...]
    columns: tuple[ReportAxisSegment, ...]
    rows: tuple[ReportAxisSegment, ...]

    @property
    def page_dimensions(self) -> tuple[str, ...]:
        """Return configurable POV dimensions in their request order."""
        return tuple(dimension for dimension, _ in self.pov)

    @property
    def row_dimensions(self) -> tuple[str, ...]:
        """Return the common row dimensions used by every row segment."""
        return self.rows[0].dimensions

    @property
    def column_dimensions(self) -> tuple[str, ...]:
        """Return the common column dimensions used by every column segment."""
        return self.columns[0].dimensions


@dataclass(frozen=True, slots=True)
class FormReportRequest:
    """Validated inputs for one Planning form report."""

    form_name: str
    output_path: Path
    title: str
    page_member_overrides: tuple[tuple[str, str], ...] = ()
    filter_members: tuple[str, ...] = ()
    overwrite: bool = False


@dataclass(frozen=True, slots=True)
class FormReportResult:
    """Details of a successfully generated Planning form report."""

    form_name: str
    output_path: Path
    generated_at: datetime
    row_count: int
    data_cell_count: int
    pov: tuple[tuple[str, str], ...] = ()

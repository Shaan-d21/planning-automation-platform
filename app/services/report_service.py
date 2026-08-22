"""Planning form report generation orchestration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from app.clients.epm_client import EPMClient
from app.models.data_validation import FormGrid, FormLayout
from app.models.report import (
    DataSliceReportDefinition,
    FormReportRequest,
    FormReportResult,
)
from app.services.data_slice_report_service import DataSliceReportService
from app.services.excel_report_renderer import ExcelFormReportRenderer
from app.services.form_service import PlanningFormService
from app.services.report_catalog_service import ReportCatalogService
from app.utils.exceptions import APIRequestError, ReportGenerationError


class FormReportService:
    """Export one Planning form and render it as an Excel report."""

    def __init__(
        self,
        client: EPMClient,
        *,
        form_service: PlanningFormService | None = None,
        data_slice_service: DataSliceReportService | None = None,
        catalog_service: ReportCatalogService | None = None,
        catalog_file: Path | None = None,
        renderer: ExcelFormReportRenderer | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._client = client
        self._form_service = form_service or PlanningFormService(client)
        self._data_slice_service = (
            data_slice_service or DataSliceReportService(client)
        )
        self._catalog_service = catalog_service or ReportCatalogService()
        self._catalog_file = catalog_file
        self._renderer = renderer or ExcelFormReportRenderer()
        self._logger = logger or logging.getLogger(__name__)

    def get_form_layout(self, form_name: str) -> FormLayout:
        """Return the form structure used to collect valid report POV."""
        definition = self._find_definition(form_name)
        if definition is not None:
            self._logger.info(
                "Using catalog data-slice layout for report '%s' on cube "
                "'%s'.",
                definition.name,
                definition.cube,
            )
            return self._definition_layout(definition)
        try:
            return self._form_service.get_form_layout(form_name)
        except APIRequestError as exc:
            if exc.status_code != 404:
                raise
            raise ReportGenerationError(
                f"Oracle could not export Planning form or form ID "
                f"'{form_name}'. The form may not be accessible to this "
                "user, or this environment may not yet support the Export "
                "Form Data REST API. Oracle Reports artifacts are not "
                "Planning forms. To generate this workbook reliably, add a "
                "matching data-slice definition to "
                f"'{self._catalog_file or 'config/reports.json'}' and select "
                "it from Registered report."
            ) from exc

    def uses_data_slice_definition(self, form_name: str) -> bool:
        """Return whether a local data-slice definition backs the report."""
        return self._find_definition(form_name) is not None

    def list_registered_reports(
        self,
    ) -> tuple[DataSliceReportDefinition, ...]:
        """Return catalog-backed reports available in this environment."""
        if self._catalog_file is None:
            return ()
        if not self._catalog_file.exists():
            return ()
        return self._catalog_service.load(self._catalog_file)

    def register_data_slice_definition(
        self,
        definition: DataSliceReportDefinition,
    ) -> DataSliceReportDefinition:
        """Validate and persist one on-premises report definition."""
        if self._catalog_file is None:
            raise ReportGenerationError(
                "A report catalog file is required to register a report."
            )
        registered = self._catalog_service.register(
            self._catalog_file,
            definition,
        )
        self._logger.info(
            "Planning report registered: name='%s', cube='%s', "
            "catalog='%s'.",
            registered.name,
            registered.cube,
            self._catalog_file,
        )
        return registered

    def get_default_title(self, form_name: str) -> str:
        """Return the configured title or a form-based default."""
        definition = self._find_definition(form_name)
        return (
            definition.title
            if definition is not None
            else f"{str(form_name).strip()} Report"
        )

    def generate(
        self,
        request: FormReportRequest,
        *,
        layout: FormLayout | None = None,
    ) -> FormReportResult:
        """Export the requested form slice and save a formatted workbook."""
        normalized = self._validate_request(request)
        resolved_layout = layout or self.get_form_layout(
            normalized.form_name
        )
        generated_at = datetime.now().astimezone()
        definition = self._find_definition(normalized.form_name)
        if definition is not None:
            grid = self._export_registered_slice(
                definition,
                normalized,
            )
        else:
            page_members = self._resolve_page_members(
                resolved_layout,
                normalized.page_member_overrides,
            )
            try:
                grid = self._form_service.export_form(
                    normalized.form_name,
                    page_members=page_members,
                    filter_members=normalized.filter_members,
                )
            except APIRequestError as exc:
                if exc.status_code != 404:
                    raise
                raise ReportGenerationError(
                    f"Oracle could not export Planning form or form ID "
                    f"'{normalized.form_name}' during report generation. "
                    "Use a registered data-slice report if the environment "
                    "does not support Export Form Data."
                ) from exc
        output_path = self._renderer.render(
            application_name=self._client.application_name,
            form_name=normalized.form_name,
            title=normalized.title,
            grid=grid,
            output_path=normalized.output_path,
            generated_at=generated_at,
            overwrite=normalized.overwrite,
        )
        result = FormReportResult(
            form_name=normalized.form_name,
            output_path=output_path,
            generated_at=generated_at,
            row_count=len(grid.rows),
            data_cell_count=len(grid.rows) * len(grid.columns),
            pov=grid.pov,
        )
        self._logger.info(
            "Planning form report generated: form='%s', output='%s', "
            "rows=%s, cells=%s.",
            result.form_name,
            result.output_path,
            result.row_count,
            result.data_cell_count,
        )
        return result

    def _export_registered_slice(
        self,
        definition: DataSliceReportDefinition,
        request: FormReportRequest,
    ) -> FormGrid:
        if request.filter_members:
            raise ReportGenerationError(
                "Additional filter members are not supported by catalog "
                "data-slice reports. Define row and column member selections "
                "in the report catalog."
            )
        resolved_pov = self._resolve_data_slice_pov(
            definition,
            request.page_member_overrides,
        )
        return self._data_slice_service.export(
            definition,
            pov=resolved_pov,
        )

    def _find_definition(
        self,
        form_name: str,
    ) -> DataSliceReportDefinition | None:
        if self._catalog_file is None:
            return None
        return self._catalog_service.find(
            self._catalog_file,
            form_name,
        )

    @staticmethod
    def _definition_layout(
        definition: DataSliceReportDefinition,
    ) -> FormLayout:
        return FormLayout(
            page_dimensions=definition.page_dimensions,
            row_dimensions=definition.row_dimensions,
            column_dimensions=definition.column_dimensions,
            pov=definition.pov,
        )

    @staticmethod
    def _resolve_data_slice_pov(
        definition: DataSliceReportDefinition,
        overrides: tuple[tuple[str, str], ...],
    ) -> tuple[tuple[str, str], ...]:
        normalized = FormReportService._normalize_overrides(overrides)
        known = {
            dimension.casefold(): dimension
            for dimension, _ in definition.pov
        }
        unknown = [
            dimension
            for dimension, _ in normalized
            if dimension.casefold() not in known
        ]
        if unknown:
            raise ReportGenerationError(
                "Report page override contains dimensions that are not on "
                f"the report POV: {', '.join(unknown)}."
            )
        override_map = {
            dimension.casefold(): member
            for dimension, member in normalized
        }
        return tuple(
            (
                dimension,
                override_map.get(dimension.casefold(), default_member),
            )
            for dimension, default_member in definition.pov
        )

    @staticmethod
    def _validate_request(request: FormReportRequest) -> FormReportRequest:
        form_name = str(request.form_name).strip()
        title = str(request.title).strip() or form_name
        output_path = Path(request.output_path).expanduser()
        if not form_name:
            raise ReportGenerationError("Planning report form is required.")
        if output_path.suffix.lower() != ".xlsx":
            raise ReportGenerationError(
                "Planning report output must use the .xlsx extension."
            )
        overrides = FormReportService._normalize_overrides(
            request.page_member_overrides
        )
        filters = tuple(
            value
            for value in (
                str(item).strip() for item in request.filter_members
            )
            if value
        )
        return FormReportRequest(
            form_name=form_name,
            output_path=output_path,
            title=title,
            page_member_overrides=overrides,
            filter_members=filters,
            overwrite=bool(request.overwrite),
        )

    @staticmethod
    def _normalize_overrides(
        overrides: tuple[tuple[str, str], ...],
    ) -> tuple[tuple[str, str], ...]:
        normalized: list[tuple[str, str]] = []
        seen: set[str] = set()
        for raw_dimension, raw_member in overrides:
            dimension = str(raw_dimension).strip()
            member = str(raw_member).strip()
            if not dimension or not member:
                raise ReportGenerationError(
                    "Each report page override requires DIMENSION=MEMBER."
                )
            key = dimension.casefold()
            if key in seen:
                raise ReportGenerationError(
                    f"Report page dimension '{dimension}' was supplied "
                    "more than once."
                )
            seen.add(key)
            normalized.append((dimension, member))
        return tuple(normalized)

    @staticmethod
    def _resolve_page_members(
        layout: FormLayout,
        overrides: tuple[tuple[str, str], ...],
    ) -> tuple[str, ...]:
        if not overrides:
            return ()

        override_map = {
            dimension.casefold(): member
            for dimension, member in overrides
        }
        known_dimensions = {
            dimension.casefold(): dimension
            for dimension in layout.page_dimensions
        }
        unknown = [
            dimension
            for dimension, _ in overrides
            if dimension.casefold() not in known_dimensions
        ]
        if unknown:
            raise ReportGenerationError(
                "Report page override contains dimensions that are not on "
                f"the form page axis: {', '.join(unknown)}."
            )

        current_pov = {
            dimension.casefold(): member
            for dimension, member in layout.pov
        }
        allowed_by_dimension: Mapping[str, tuple[str, ...]] = {
            dimension.casefold(): members
            for dimension, members in layout.allowed_page_members
        }
        resolved: list[str] = []
        for dimension in layout.page_dimensions:
            key = dimension.casefold()
            member = override_map.get(key) or current_pov.get(key)
            if not member:
                raise ReportGenerationError(
                    f"No page member was supplied for '{dimension}', and "
                    "the form did not return a default POV member."
                )
            allowed = allowed_by_dimension.get(key, ())
            if allowed and member.casefold() not in {
                item.casefold() for item in allowed
            }:
                raise ReportGenerationError(
                    f"Member '{member}' is not an allowed page member for "
                    f"dimension '{dimension}'."
                )
            resolved.append(member)
        return tuple(resolved)

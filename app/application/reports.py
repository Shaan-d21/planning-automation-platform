"""Application use cases for Planning report discovery and generation."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.clients.epm_client import EPMClient
from app.config.settings import Settings
from app.models.report import (
    DataSliceReportDefinition,
    FormReportRequest,
)
from app.services.report_catalog_service import ReportCatalogService
from app.services.report_service import FormReportService
from app.utils.exceptions import ReportGenerationError


@dataclass(frozen=True, slots=True)
class ReportCatalogItem:
    """Presentation-safe registered report summary."""

    name: str
    title: str
    cube: str
    default_pov: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class ReportPreflight:
    """Live form or registered data-slice layout for user input."""

    form_name: str
    title: str
    cube: str | None
    registered: bool
    page_dimensions: tuple[str, ...]
    row_dimensions: tuple[str, ...]
    column_dimensions: tuple[str, ...]
    current_pov: tuple[tuple[str, str], ...]
    allowed_page_members: tuple[tuple[str, tuple[str, ...]], ...]


@dataclass(frozen=True, slots=True)
class ReportGenerationOperationInput:
    """Approved standalone report-generation inputs."""

    form_name: str
    title: str
    page_member_overrides: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ReportGenerationResult:
    """Generated workbook details returned to the workflow."""

    form_name: str
    output_path: Path
    row_count: int
    data_cell_count: int
    pov: tuple[tuple[str, str], ...]


class ReportWorkspaceService:
    """Discover reports, preflight layouts, and generate safe artifacts."""

    def __init__(
        self,
        settings: Settings,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._settings = settings
        self._logger = logger or logging.getLogger(__name__)

    def catalog(self) -> tuple[ReportCatalogItem, ...]:
        """Return administrator-registered report definitions."""
        definitions = ReportCatalogService().load(
            self._settings.report_catalog_file
        )
        return tuple(
            ReportCatalogItem(
                name=item.name,
                title=item.title,
                cube=item.cube,
                default_pov=item.pov,
            )
            for item in definitions
        )

    def register(
        self,
        definition: DataSliceReportDefinition,
    ) -> ReportCatalogItem:
        """Validate and persist one approved data-slice report definition."""
        registered = ReportCatalogService().register(
            self._settings.report_catalog_file,
            definition,
        )
        self._logger.info(
            "Report definition registered: name='%s', cube='%s'.",
            registered.name,
            registered.cube,
        )
        return ReportCatalogItem(
            name=registered.name,
            title=registered.title,
            cube=registered.cube,
            default_pov=registered.pov,
        )

    def preflight(self, form_name: str) -> ReportPreflight:
        """Retrieve the live or fallback report layout without generating."""
        normalized_name = self._normalize_name(form_name)
        definitions = ReportCatalogService().load(
            self._settings.report_catalog_file
        )
        definition = next(
            (
                item
                for item in definitions
                if item.name.casefold() == normalized_name.casefold()
            ),
            None,
        )
        if definition is not None:
            return ReportPreflight(
                form_name=definition.name,
                title=definition.title,
                cube=definition.cube,
                registered=True,
                page_dimensions=definition.page_dimensions,
                row_dimensions=definition.row_dimensions,
                column_dimensions=definition.column_dimensions,
                current_pov=definition.pov,
                allowed_page_members=(),
            )

        with EPMClient(
            self._settings,
            logger=self._logger.getChild("client"),
        ) as client:
            client.authenticate()
            service = self._report_service(client)
            layout = service.get_form_layout(normalized_name)
            return ReportPreflight(
                form_name=normalized_name,
                title=service.get_default_title(normalized_name),
                cube=None,
                registered=False,
                page_dimensions=layout.page_dimensions,
                row_dimensions=layout.row_dimensions,
                column_dimensions=layout.column_dimensions,
                current_pov=layout.pov,
                allowed_page_members=layout.allowed_page_members,
            )

    def generate(
        self,
        client: EPMClient,
        operation_input: ReportGenerationOperationInput,
        *,
        execution_id: str,
    ) -> ReportGenerationResult:
        """Generate one unique Excel artifact and return its details."""
        normalized = self.normalize_input(operation_input)
        service = self._report_service(client)
        output_path = self._output_path(
            normalized.form_name,
            execution_id=execution_id,
        )
        result = service.generate(
            FormReportRequest(
                form_name=normalized.form_name,
                output_path=output_path,
                title=normalized.title,
                page_member_overrides=(
                    normalized.page_member_overrides
                ),
                overwrite=False,
            )
        )
        return ReportGenerationResult(
            form_name=result.form_name,
            output_path=result.output_path,
            row_count=result.row_count,
            data_cell_count=result.data_cell_count,
            pov=result.pov,
        )

    @staticmethod
    def normalize_input(
        operation_input: ReportGenerationOperationInput,
    ) -> ReportGenerationOperationInput:
        """Normalize report inputs before Oracle is contacted."""
        form_name = ReportWorkspaceService._normalize_name(
            operation_input.form_name
        )
        title = str(operation_input.title).strip() or form_name
        if len(title) > 250:
            raise ReportGenerationError(
                "Planning report title cannot exceed 250 characters."
            )
        overrides: list[tuple[str, str]] = []
        seen: set[str] = set()
        for raw_dimension, raw_member in (
            operation_input.page_member_overrides
        ):
            dimension = str(raw_dimension).strip()
            member = str(raw_member).strip()
            if not dimension or not member:
                raise ReportGenerationError(
                    "Each report POV override requires a dimension and "
                    "member."
                )
            key = dimension.casefold()
            if key in seen:
                raise ReportGenerationError(
                    f"Report POV dimension '{dimension}' was supplied more "
                    "than once."
                )
            seen.add(key)
            overrides.append((dimension, member))
        return ReportGenerationOperationInput(
            form_name=form_name,
            title=title,
            page_member_overrides=tuple(overrides),
        )

    @staticmethod
    def _normalize_name(form_name: str) -> str:
        normalized = str(form_name).strip()
        if not normalized:
            raise ReportGenerationError(
                "Planning form or registered report name is required."
            )
        if len(normalized) > 250:
            raise ReportGenerationError(
                "Planning form or report name cannot exceed 250 characters."
            )
        return normalized

    def _report_service(self, client: EPMClient) -> FormReportService:
        return FormReportService(
            client,
            catalog_file=self._settings.report_catalog_file,
            logger=self._logger.getChild("report_service"),
        )

    def _output_path(
        self,
        form_name: str,
        *,
        execution_id: str,
    ) -> Path:
        safe_name = re.sub(
            r"[^A-Za-z0-9._-]+",
            "_",
            form_name,
        ).strip("._")
        if not safe_name:
            safe_name = "Planning_Report"
        timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
        execution_suffix = re.sub(
            r"[^A-Za-z0-9]+",
            "",
            execution_id,
        )[:8]
        filename = (
            f"{safe_name}_{timestamp}"
            f"{f'_{execution_suffix}' if execution_suffix else ''}.xlsx"
        )
        return self._settings.report_output_dir / filename

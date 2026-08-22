"""Export report grids through the supported Planning data-slice API."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

from app.clients.epm_client import EPMClient
from app.models.data_validation import FormGrid, FormGridRow
from app.models.report import DataSliceReportDefinition, ReportAxisSegment
from app.utils.exceptions import APIRequestError, ReportGenerationError


class DataSliceReportService:
    """Export a catalog-defined cube slice as a normalized report grid."""

    def __init__(
        self,
        client: EPMClient,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._client = client
        self._logger = logger or logging.getLogger(__name__)
        application = quote(client.application_name, safe="")
        self._plan_types_endpoint = (
            f"{client.planning_api_root}/applications/{application}/plantypes"
        )

    def export(
        self,
        definition: DataSliceReportDefinition,
        *,
        pov: tuple[tuple[str, str], ...],
    ) -> FormGrid:
        """Export the definition with resolved POV values."""
        endpoint = (
            f"{self._plan_types_endpoint}/"
            f"{quote(definition.cube, safe='')}/exportdataslice"
        )
        response = self._client.post(
            endpoint,
            payload={
                "exportPlanningData": False,
                "gridDefinition": {
                    "suppressMissingBlocks": True,
                    "pov": {
                        "dimensions": [
                            dimension for dimension, _ in pov
                        ],
                        "members": [[member] for _, member in pov],
                    },
                    "columns": [
                        self._segment_payload(segment)
                        for segment in definition.columns
                    ],
                    "rows": [
                        self._segment_payload(segment)
                        for segment in definition.rows
                    ],
                },
            },
        )
        if not isinstance(response, Mapping):
            raise APIRequestError(
                f"Oracle returned an unexpected data slice for report "
                f"'{definition.name}'."
            )
        grid = self._normalize_grid(response, definition, pov)
        self._logger.info(
            "Planning data-slice report exported: report='%s', cube='%s', "
            "rows=%s, columns=%s.",
            definition.name,
            definition.cube,
            len(grid.rows),
            len(grid.columns),
        )
        return grid

    @staticmethod
    def _segment_payload(
        segment: ReportAxisSegment,
    ) -> dict[str, list[Any]]:
        return {
            "dimensions": list(segment.dimensions),
            "members": [
                list(selection) for selection in segment.members
            ],
        }

    def _normalize_grid(
        self,
        response: Mapping[str, Any],
        definition: DataSliceReportDefinition,
        pov: tuple[tuple[str, str], ...],
    ) -> FormGrid:
        raw_columns = response.get("columns")
        raw_rows = response.get("rows")
        if not self._is_sequence(raw_columns) or not self._is_sequence(
            raw_rows
        ):
            raise ReportGenerationError(
                f"Data-slice response for report '{definition.name}' did "
                "not contain rows and columns."
            )

        column_axes = tuple(
            tuple(str(member) for member in axis)
            for axis in raw_columns
            if self._is_sequence(axis)
        )
        if len(column_axes) != len(raw_columns):
            raise ReportGenerationError(
                f"Data-slice response for report '{definition.name}' "
                "contains an invalid column axis."
            )
        column_count = len(column_axes[0]) if column_axes else 0
        if any(len(axis) != column_count for axis in column_axes):
            raise ReportGenerationError(
                f"Data-slice response for report '{definition.name}' has "
                "misaligned column axes."
            )
        columns = tuple(
            tuple(axis[index] for axis in column_axes)
            for index in range(column_count)
        )

        rows: list[FormGridRow] = []
        for raw_row in raw_rows:
            if not isinstance(raw_row, Mapping):
                raise ReportGenerationError(
                    f"Data-slice response for report '{definition.name}' "
                    "contains an invalid row."
                )
            headers = raw_row.get("headers")
            data = raw_row.get("data")
            if not self._is_sequence(headers) or not self._is_sequence(data):
                raise ReportGenerationError(
                    f"Data-slice response for report '{definition.name}' "
                    "contains an incomplete row."
                )
            if len(data) != column_count:
                raise ReportGenerationError(
                    f"Data-slice response for report '{definition.name}' "
                    "contains row data that does not align with its columns."
                )
            rows.append(
                FormGridRow(
                    headers=tuple(str(value) for value in headers),
                    data=tuple(data),
                )
            )

        return FormGrid(
            row_dimensions=definition.row_dimensions,
            column_dimensions=definition.column_dimensions,
            columns=columns,
            rows=tuple(rows),
            pov=pov,
        )

    @staticmethod
    def _is_sequence(value: Any) -> bool:
        return isinstance(value, Sequence) and not isinstance(
            value,
            (str, bytes),
        )

"""Reusable access to Oracle Planning form layouts and data grids."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

from app.clients.epm_client import EPMClient
from app.models.data_validation import FormGrid, FormLayout
from app.utils.exceptions import APIRequestError, DataValidationError


class PlanningFormService:
    """Discover and export Planning forms through the shared REST client."""

    def __init__(
        self,
        client: EPMClient,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._client = client
        self._logger = logger or logging.getLogger(__name__)
        application = quote(client.application_name, safe="")
        self._forms_endpoint = (
            f"{client.planning_api_root}/applications/{application}/forms"
        )

    def get_form_layout(self, form_name: str) -> FormLayout:
        """Retrieve form axes, current POV, and allowed page members."""
        name = self._normalize_name(form_name)
        response = self._client.get(
            f"{self._forms_endpoint}/{quote(name, safe='')}/data",
            params={
                "displayMemberAs": "MEMBER_NAME",
                "fields": "gridInfo,pov",
            },
        )
        if not isinstance(response, Mapping):
            raise APIRequestError(
                f"Oracle returned an unexpected layout for form '{name}'."
            )
        layout = FormLayout.from_response(response)
        self._logger.info(
            "Planning form layout retrieved: form='%s', page_dimensions=%s, "
            "row_dimensions=%s, column_dimensions=%s.",
            name,
            layout.page_dimensions,
            layout.row_dimensions,
            layout.column_dimensions,
        )
        return layout

    def export_form(
        self,
        form_name: str,
        *,
        page_members: Sequence[str] = (),
        filter_members: Sequence[str] = (),
    ) -> FormGrid:
        """Export a Planning form as a normalized JSON grid."""
        name = self._normalize_name(form_name)
        params: dict[str, Any] = {
            "displayMemberAs": "MEMBER_NAME",
            "forceStartExpanded": "true",
        }
        if page_members:
            params["pageMbrList"] = list(page_members)
        if filter_members:
            params["filterMembers"] = list(filter_members)
        response = self._client.get(
            f"{self._forms_endpoint}/{quote(name, safe='')}/data",
            params=params,
        )
        if not isinstance(response, Mapping):
            raise APIRequestError(
                f"Oracle returned an unexpected export for form '{name}'."
            )
        grid = FormGrid.from_response(response)
        self._logger.info(
            "Planning form exported: form='%s', rows=%s, columns=%s.",
            name,
            len(grid.rows),
            len(grid.columns),
        )
        return grid

    @staticmethod
    def _normalize_name(form_name: str) -> str:
        name = str(form_name).strip()
        if not name:
            raise DataValidationError("Planning form name cannot be empty.")
        return name


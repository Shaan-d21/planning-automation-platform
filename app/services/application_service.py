"""Read-only Oracle Planning application and cube discovery."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import replace
from urllib.parse import quote

from app.clients.epm_client import EPMClient
from app.models.environment import (
    ApplicationInfo,
    DimensionInfo,
    MemberInfo,
    PlanTypeInfo,
)
from app.utils.exceptions import APIRequestError


class ApplicationService:
    """Fetch application metadata exposed by the connected Planning version."""

    _COMPATIBILITY_STATUS_CODES = frozenset({400, 404, 405, 501})

    def __init__(
        self,
        client: EPMClient,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._client = client
        self._logger = logger or logging.getLogger(__name__)
        self._applications_endpoint = (
            f"{client.planning_api_root}/applications"
        )

    def get_configured_application(self) -> ApplicationInfo:
        """Return metadata for the exact configured Planning application."""
        name = quote(self._client.application_name, safe="")
        response = self._client.get(
            f"{self._applications_endpoint}/{name}"
        )
        if not isinstance(response, Mapping):
            raise APIRequestError(
                "Oracle Planning returned unexpected application metadata."
            )
        application = ApplicationInfo.from_response(response)
        if (
            application.name.casefold()
            != self._client.application_name.casefold()
        ):
            raise APIRequestError(
                "Oracle Planning returned metadata for a different "
                f"application: '{application.name}'."
            )
        return application

    def get_applications(self) -> tuple[ApplicationInfo, ...]:
        """Return all applications visible to the connected user."""
        response = self._client.get(self._applications_endpoint)
        items = self._items(response, "applications")
        return tuple(
            sorted(
                (
                    ApplicationInfo.from_response(item)
                    for item in items
                    if isinstance(item, Mapping)
                ),
                key=lambda item: item.name.casefold(),
            )
        )

    def get_plan_types(
        self,
        *,
        include_dimensions: bool = False,
    ) -> tuple[PlanTypeInfo, ...]:
        """Return plan types when supported by the connected Cloud version."""
        application = quote(self._client.application_name, safe="")
        endpoint = (
            f"{self._applications_endpoint}/{application}/plantypes"
        )
        try:
            response = self._client.get(endpoint)
            items = self._items(response, "plan types")
        except APIRequestError as exc:
            if not self._can_use_legacy_discovery(exc):
                raise
            plan_types = self._discover_legacy_plan_types()
            if not plan_types:
                raise
            self._logger.warning(
                "Get Plan Types is unavailable; discovered %s Planning "
                "cubes from live Oracle substitution-variable scopes and "
                "job definitions.",
                len(plan_types),
            )
            return plan_types
        plan_types = tuple(
            sorted(
                (
                    PlanTypeInfo.from_response(item)
                    for item in items
                    if isinstance(item, Mapping)
                ),
                key=lambda item: item.name.casefold(),
            )
        )
        if not include_dimensions:
            return plan_types
        return tuple(
            replace(
                plan_type,
                dimensions=self.get_dimensions(plan_type.name),
            )
            for plan_type in plan_types
        )

    def _discover_legacy_plan_types(self) -> tuple[PlanTypeInfo, ...]:
        """Discover cubes from older, live application-scoped resources."""
        names: dict[str, str] = {}
        sources = (
            (
                f"{self._applications_endpoint}/"
                f"{quote(self._client.application_name, safe='')}/"
                "substitutionvariables",
                ("planType", "planTypeName", "cubeName"),
                ("items", "substitutionVariables"),
            ),
            (
                f"{self._applications_endpoint}/"
                f"{quote(self._client.application_name, safe='')}/"
                "jobdefinitions",
                ("planTypeName", "cubeName", "planType"),
                ("items", "jobDefinitions"),
            ),
        )
        for endpoint, fields, collection_names in sources:
            try:
                items = self._legacy_items(
                    endpoint,
                    collection_names=collection_names,
                )
            except APIRequestError as exc:
                self._logger.warning(
                    "Oracle cube-discovery source '%s' was unavailable: %s",
                    endpoint.rsplit("/", 1)[-1],
                    exc,
                )
                continue
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                name = next(
                    (
                        str(item.get(field, "")).strip()
                        for field in fields
                        if str(item.get(field, "")).strip()
                    ),
                    "",
                )
                if (
                    not name
                    or name.isdecimal()
                    or name.casefold()
                    in {
                        "all",
                        self._client.application_name.casefold(),
                    }
                ):
                    continue
                names.setdefault(name.casefold(), name)
        return tuple(
            PlanTypeInfo(name=name, cube_name=name)
            for name in sorted(names.values(), key=str.casefold)
        )

    def _legacy_items(
        self,
        endpoint: str,
        *,
        collection_names: tuple[str, ...],
    ) -> list:
        """Read one legacy collection across common on-prem response shapes."""
        try:
            response = self._client.get(
                endpoint,
                params={"limit": -1},
            )
        except APIRequestError as exc:
            if exc.status_code not in {400, 405}:
                raise
            # Some older Planning deployments expose the resource but reject
            # cloud pagination parameters. Retry the same read without them.
            response = self._client.get(endpoint)

        if isinstance(response, list):
            return response
        if not isinstance(response, Mapping):
            raise APIRequestError(
                "Oracle Planning returned unexpected cube discovery metadata."
            )
        for name in collection_names:
            items = response.get(name)
            if isinstance(items, list):
                return items
        raise APIRequestError(
            "Oracle Planning cube discovery response did not contain a "
            "recognized collection."
        )

    def _can_use_legacy_discovery(self, error: APIRequestError) -> bool:
        """Return whether a plan-type failure denotes an older API surface."""
        if error.status_code == 404:
            return True
        return (
            not bool(self._client.is_cloud_environment)
            and error.status_code in self._COMPATIBILITY_STATUS_CODES
        )

    def get_dimensions(
        self,
        plan_type: str,
    ) -> tuple[DimensionInfo, ...]:
        """Return dimensions for one plan type."""
        normalized = str(plan_type).strip()
        if not normalized:
            raise APIRequestError("Plan type name cannot be empty.")
        application = quote(self._client.application_name, safe="")
        endpoint = (
            f"{self._applications_endpoint}/{application}/plantypes/"
            f"{quote(normalized, safe='')}/dimensions"
        )
        response = self._client.get(
            endpoint,
            params={
                "limit": -1,
                "fields": "name,dimType",
            },
        )
        items = self._items(response, "dimensions")
        return tuple(
            DimensionInfo.from_response(item)
            for item in items
            if isinstance(item, Mapping)
        )

    def get_dimension_members(
        self,
        plan_type: str,
        dimension: str,
    ) -> tuple[MemberInfo, ...]:
        """Return the flattened member hierarchy for one plan-type dimension."""
        normalized_plan_type = str(plan_type).strip()
        normalized_dimension = str(dimension).strip()
        if not normalized_plan_type:
            raise APIRequestError("Plan type name cannot be empty.")
        if not normalized_dimension:
            raise APIRequestError("Dimension name cannot be empty.")
        application = quote(self._client.application_name, safe="")
        endpoint = (
            f"{self._applications_endpoint}/{application}/plantypes/"
            f"{quote(normalized_plan_type, safe='')}/dimensions/"
            f"{quote(normalized_dimension, safe='')}"
        )
        response = self._client.get(
            endpoint,
            params={
                "fields": "name,alias,path,parentName,children",
                "aliasTableName": "Default",
            },
        )
        if not isinstance(response, Mapping):
            raise APIRequestError(
                "Oracle Planning returned unexpected dimension hierarchy "
                "metadata."
            )
        children = response.get("children")
        if children is None:
            return ()
        if not isinstance(children, list):
            raise APIRequestError(
                "Oracle Planning dimension hierarchy did not contain a "
                "children collection."
            )
        members: list[MemberInfo] = []
        seen: set[str] = set()

        def visit(items: list, parent_name: str | None) -> None:
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                name = str(item.get("name", "")).strip()
                nested = item.get("children")
                nested_children = nested if isinstance(nested, list) else []
                if name and name.casefold() not in seen:
                    seen.add(name.casefold())
                    members.append(
                        MemberInfo(
                            name=name,
                            alias=_optional_text(item.get("alias")),
                            path=_optional_text(
                                item.get("displayPath") or item.get("path")
                            ),
                            parent_name=_optional_text(
                                item.get("parentName") or parent_name
                            ),
                            has_children=bool(nested_children),
                        )
                    )
                visit(nested_children, name or parent_name)

        visit(children, normalized_dimension)
        return tuple(members)

    @staticmethod
    def _items(response, label: str) -> list:
        if not isinstance(response, Mapping):
            raise APIRequestError(
                f"Oracle Planning returned unexpected {label} metadata."
            )
        items = response.get("items")
        if not isinstance(items, list):
            raise APIRequestError(
                f"Oracle Planning {label} response did not contain an "
                "items collection."
            )
        return items


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None

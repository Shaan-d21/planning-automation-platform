"""Discovery and safe updates for Oracle Planning substitution variables."""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Mapping, Sequence
from urllib.parse import quote

from app.clients.epm_client import EPMClient
from app.models.substitution_variable import (
    PlanType,
    RequestedSubstitutionVariableUpdate,
    SubstitutionVariable,
    SubstitutionVariableUpdate,
)
from app.utils.exceptions import (
    APIRequestError,
    SubstitutionVariableError,
)


class SubstitutionVariableService:
    """Retrieve variables across scopes and update discovered definitions."""

    def __init__(
        self,
        client: EPMClient,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._client = client
        self._logger = logger or logging.getLogger(__name__)
        application = quote(client.application_name, safe="")
        self._application_endpoint = (
            f"{client.planning_api_root}/applications/{application}"
        )
        self._variables_endpoint = (
            f"{self._application_endpoint}/substitutionvariables"
        )

    def get_plan_types(self) -> tuple[PlanType, ...]:
        """Retrieve current Planning cubes and their storage types."""
        try:
            response = self._client.get(
                f"{self._application_endpoint}/plantypes"
            )
        except APIRequestError as exc:
            if exc.status_code != 404:
                raise
            self._logger.warning(
                "Get Plan Types is unavailable in this Planning version; "
                "deriving visible scopes from substitution variables."
            )
            scopes = sorted(
                {
                    variable.scope
                    for variable in self.get_all_variables()
                    if variable.scope.casefold() != "all"
                },
                key=str.casefold,
            )
            return tuple(
                PlanType(
                    name=scope,
                    cube_name=scope,
                    identifier=0,
                    cube_type=0,
                    dimension_count=0,
                )
                for scope in scopes
            )
        items = self._response_items(response, "plan types")
        return tuple(
            sorted(
                (
                    PlanType.from_response(item)
                    for item in items
                    if isinstance(item, Mapping)
                ),
                key=lambda item: item.cube_name.casefold(),
            )
        )

    def get_all_variables(self) -> tuple[SubstitutionVariable, ...]:
        """Retrieve variables defined at ALL and individual cube scopes."""
        response = self._client.get(self._variables_endpoint)
        items = self._response_items(response, "substitution variables")
        variables = tuple(
            SubstitutionVariable.from_response(item)
            for item in items
            if isinstance(item, Mapping)
        )
        return tuple(
            sorted(
                variables,
                key=lambda item: (
                    item.scope.casefold() != "all",
                    item.scope.casefold(),
                    item.name.casefold(),
                ),
            )
        )

    def build_updates(
        self,
        requested_values: Mapping[tuple[str, str], str],
        *,
        current_variables: Sequence[SubstitutionVariable] | None = None,
    ) -> tuple[SubstitutionVariableUpdate, ...]:
        """Build updates only for variable definitions Oracle returned."""
        current = (
            tuple(current_variables)
            if current_variables is not None
            else self.get_all_variables()
        )
        by_key = {variable.key: variable for variable in current}
        updates: list[SubstitutionVariableUpdate] = []
        for (raw_scope, raw_name), raw_value in requested_values.items():
            scope = str(raw_scope).strip()
            name = str(raw_name).strip()
            new_value = str(raw_value).strip()
            if not scope or not name or not new_value:
                raise SubstitutionVariableError(
                    "Variable scope, name, and new value are required."
                )
            variable = by_key.get((scope.casefold(), name.casefold()))
            if variable is None:
                raise SubstitutionVariableError(
                    f"Substitution variable '{scope}.{name}' was not "
                    "discovered and will not be created automatically."
                )
            updates.append(
                SubstitutionVariableUpdate(
                    name=variable.name,
                    scope=variable.scope,
                    old_value=variable.value,
                    new_value=new_value,
                )
            )
        return tuple(
            sorted(
                updates,
                key=lambda item: (
                    item.scope.casefold(),
                    item.name.casefold(),
                ),
            )
        )

    def build_safe_updates(
        self,
        requests: Sequence[RequestedSubstitutionVariableUpdate],
        *,
        current_variables: Sequence[SubstitutionVariable] | None = None,
    ) -> tuple[SubstitutionVariableUpdate, ...]:
        """Resolve selected updates and reject stale browser values."""
        current = (
            tuple(current_variables)
            if current_variables is not None
            else self.get_all_variables()
        )
        by_key = {variable.key: variable for variable in current}
        requested_values: dict[tuple[str, str], str] = {}
        for request in requests:
            scope = str(request.scope).strip()
            name = str(request.name).strip()
            key = (scope.casefold(), name.casefold())
            variable = by_key.get(key)
            if variable is None:
                raise SubstitutionVariableError(
                    f"Substitution variable '{scope}.{name}' no longer "
                    "exists. Refresh the selection before running."
                )
            if variable.value != str(request.expected_current_value):
                raise SubstitutionVariableError(
                    f"Substitution variable '{variable.scope}."
                    f"{variable.name}' changed after it was selected. "
                    "Refresh the variable list before running."
                )
            requested_values[(variable.scope, variable.name)] = (
                request.new_value
            )
        return self.build_updates(
            requested_values,
            current_variables=current,
        )

    @staticmethod
    def merge_updates(
        *groups: Sequence[SubstitutionVariableUpdate],
    ) -> tuple[SubstitutionVariableUpdate, ...]:
        """Merge update sources while rejecting contradictory values."""
        merged: dict[tuple[str, str], SubstitutionVariableUpdate] = {}
        for group in groups:
            for update in group:
                key = (update.scope.casefold(), update.name.casefold())
                existing = merged.get(key)
                if existing and existing.new_value != update.new_value:
                    raise SubstitutionVariableError(
                        f"Substitution variable '{update.scope}."
                        f"{update.name}' has conflicting requested values "
                        f"'{existing.new_value}' and '{update.new_value}'."
                    )
                merged[key] = update
        return tuple(
            sorted(
                merged.values(),
                key=lambda item: (
                    item.scope.casefold(),
                    item.name.casefold(),
                ),
            )
        )

    def apply_updates(
        self,
        updates: Sequence[SubstitutionVariableUpdate],
    ) -> tuple[SubstitutionVariableUpdate, ...]:
        """Apply changed values, grouped by their exact Oracle scope."""
        changed = tuple(update for update in updates if update.is_changed)
        grouped: dict[str, list[SubstitutionVariableUpdate]] = defaultdict(
            list
        )
        for update in changed:
            grouped[update.scope].append(update)

        for scope, scope_updates in grouped.items():
            endpoint = self._scope_endpoint(scope)
            payload = {
                "items": [
                    {
                        "name": update.name,
                        "value": update.new_value,
                        "planType": update.scope,
                    }
                    for update in scope_updates
                ]
            }
            self._logger.info(
                "Updating substitution variables: scope='%s', names=%s.",
                scope,
                [update.name for update in scope_updates],
            )
            self._client.post(endpoint, payload=payload)

        if changed:
            refreshed = {
                variable.key: variable
                for variable in self.get_all_variables()
            }
            failed = [
                f"{update.scope}.{update.name}"
                for update in changed
                if (
                    refreshed.get(
                        (
                            update.scope.casefold(),
                            update.name.casefold(),
                        )
                    )
                    is None
                    or refreshed[
                        (
                            update.scope.casefold(),
                            update.name.casefold(),
                        )
                    ].value
                    != update.new_value
                )
            ]
            if failed:
                raise SubstitutionVariableError(
                    "Oracle did not confirm the requested value for: "
                    + ", ".join(failed)
                )
        return changed

    def create_variable(
        self,
        scope: str,
        name: str,
        value: str,
        *,
        current_variables: Sequence[SubstitutionVariable] | None = None,
    ) -> SubstitutionVariable:
        """Explicitly create one new variable and verify it afterward."""
        normalized_scope = str(scope).strip()
        normalized_name = str(name).strip()
        normalized_value = str(value).strip()
        self.validate_definition(
            normalized_scope,
            normalized_name,
            normalized_value,
        )
        current = (
            tuple(current_variables)
            if current_variables is not None
            else self.get_all_variables()
        )
        key = (
            normalized_scope.casefold(),
            normalized_name.casefold(),
        )
        if any(variable.key == key for variable in current):
            raise SubstitutionVariableError(
                f"Substitution variable '{normalized_scope}."
                f"{normalized_name}' already exists. Use Update Existing."
            )

        self._logger.info(
            "Creating substitution variable: scope='%s', name='%s'.",
            normalized_scope,
            normalized_name,
        )
        self._client.post(
            self._scope_endpoint(normalized_scope),
            payload={
                "items": [
                    {
                        "name": normalized_name,
                        "value": normalized_value,
                        "planType": normalized_scope,
                    }
                ]
            },
        )
        refreshed = {
            variable.key: variable
            for variable in self.get_all_variables()
        }
        created = refreshed.get(key)
        if created is None or created.value != normalized_value:
            raise SubstitutionVariableError(
                f"Oracle did not confirm creation of "
                f"'{normalized_scope}.{normalized_name}'."
            )
        return created

    def _scope_endpoint(self, scope: str) -> str:
        if scope.casefold() == "all":
            return self._variables_endpoint
        return (
            f"{self._application_endpoint}/plantypes/"
            f"{quote(scope, safe='')}/substitutionvariables"
        )

    @staticmethod
    def validate_definition(
        scope: str,
        name: str,
        value: str,
    ) -> None:
        """Validate one scoped substitution-variable definition."""
        if not scope:
            raise SubstitutionVariableError(
                "Substitution variable scope cannot be empty."
            )
        if not name:
            raise SubstitutionVariableError(
                "Substitution variable name cannot be empty."
            )
        if name.startswith("&"):
            raise SubstitutionVariableError(
                "Enter the variable name without the leading '&'."
            )
        if len(name) > 80:
            raise SubstitutionVariableError(
                "Substitution variable name cannot exceed 80 characters."
            )
        if not value:
            raise SubstitutionVariableError(
                "Substitution variable value cannot be empty."
            )
        if len(value) > 255:
            raise SubstitutionVariableError(
                "Substitution variable value cannot exceed 255 characters."
            )

    @staticmethod
    def _response_items(
        response: object,
        label: str,
    ) -> list[object]:
        if not isinstance(response, Mapping):
            raise APIRequestError(
                f"Oracle returned unexpected {label}."
            )
        items = response.get("items")
        if not isinstance(items, list):
            raise APIRequestError(
                f"Oracle {label} response did not contain items."
            )
        return items

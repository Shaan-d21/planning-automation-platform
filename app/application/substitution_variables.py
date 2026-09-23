"""Application use cases for governed substitution-variable maintenance."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

from app.clients.epm_client import EPMClient
from app.config.settings import Settings
from app.models.substitution_variable import PlanType, SubstitutionVariable
from app.services.substitution_variable_service import (
    SubstitutionVariableService,
)
from app.services.variable_value_validation_service import (
    VariableValueValidationError,
    VariableValueValidationService,
)
from app.utils.exceptions import SubstitutionVariableError


class SubstitutionVariableAction(StrEnum):
    """Supported variable-maintenance actions."""

    UPDATE = "UPDATE"
    CREATE = "CREATE"


@dataclass(frozen=True, slots=True)
class SubstitutionVariableOperationInput:
    """Approved input for one substitution-variable change."""

    action: SubstitutionVariableAction
    scope: str
    name: str
    value: str
    expected_current_value: str | None = None


@dataclass(frozen=True, slots=True)
class SubstitutionVariableCatalog:
    """Live variables and valid scopes visible to the connected user."""

    variables: tuple[SubstitutionVariable, ...]
    plan_types: tuple[PlanType, ...]
    scopes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SubstitutionVariableChangeResult:
    """Verified result of one variable-maintenance request."""

    action: SubstitutionVariableAction
    scope: str
    name: str
    old_value: str | None
    new_value: str
    changed: bool


class SubstitutionVariableApplicationService:
    """Discover and safely mutate scoped Planning variables."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: EPMClient | None = None,
        value_validator: VariableValueValidationService | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        if settings is None and client is None:
            raise ValueError("Settings or an authenticated client is required.")
        self._settings = settings
        self._client = client
        self._value_validator = value_validator
        self._logger = logger or logging.getLogger(__name__)

    def discover(self) -> SubstitutionVariableCatalog:
        """Retrieve current variable definitions and available scopes."""
        if self._client is not None:
            return self._discover_with_client(self._client)
        if self._settings is None:
            raise ValueError("Settings are required for live discovery.")
        with EPMClient(
            self._settings,
            logger=self._logger.getChild("client"),
        ) as client:
            client.authenticate()
            return self._discover_with_client(client)

    def validate_value_compatibility(
        self,
        *,
        variable_name: str,
        current_value: str | None,
        proposed_value: str,
        scope: str = "ALL",
    ) -> None:
        """Validate strong local type facts without listing live members."""
        validate = (
            VariableValueValidationService
            .validate_substitution_variable_without_live_metadata
        )
        validate(
            variable_name=variable_name,
            current_value=current_value,
            proposed_value=proposed_value,
            scope=scope,
        )

    def apply(
        self,
        operation_input: SubstitutionVariableOperationInput,
    ) -> SubstitutionVariableChangeResult:
        """Apply and verify one approved update or creation."""
        if self._client is None:
            raise ValueError(
                "An authenticated client is required to apply a change."
            )
        normalized = self.normalize_input(operation_input)
        service = SubstitutionVariableService(
            self._client,
            logger=self._logger.getChild("service"),
        )
        current = service.get_all_variables()
        existing = next(
            (
                variable
                for variable in current
                if variable.key
                == (
                    normalized.scope.casefold(),
                    normalized.name.casefold(),
                )
            ),
            None,
        )

        if normalized.action is SubstitutionVariableAction.CREATE:
            try:
                validate = (
                    VariableValueValidationService
                    .validate_substitution_variable_without_live_metadata
                )
                validate(
                    variable_name=normalized.name,
                    current_value=None,
                    proposed_value=normalized.value,
                    scope=normalized.scope,
                )
            except VariableValueValidationError as exc:
                raise SubstitutionVariableError(str(exc)) from exc
            created = service.create_variable(
                normalized.scope,
                normalized.name,
                normalized.value,
                current_variables=current,
            )
            return SubstitutionVariableChangeResult(
                action=normalized.action,
                scope=created.scope,
                name=created.name,
                old_value=None,
                new_value=created.value,
                changed=True,
            )

        if existing is None:
            raise SubstitutionVariableError(
                f"Substitution variable '{normalized.scope}."
                f"{normalized.name}' no longer exists."
            )
        if (
            normalized.expected_current_value is not None
            and existing.value != normalized.expected_current_value
        ):
            raise SubstitutionVariableError(
                f"Substitution variable '{existing.scope}.{existing.name}' "
                "changed after this page was loaded. Refresh the catalog "
                "before applying another update."
            )
        try:
            validate = (
                VariableValueValidationService
                .validate_substitution_variable_without_live_metadata
            )
            validate(
                variable_name=existing.name,
                current_value=existing.value,
                proposed_value=normalized.value,
                scope=existing.scope,
            )
        except VariableValueValidationError as exc:
            raise SubstitutionVariableError(str(exc)) from exc
        updates = service.build_updates(
            {(existing.scope, existing.name): normalized.value},
            current_variables=current,
        )
        changed = service.apply_updates(updates)
        return SubstitutionVariableChangeResult(
            action=normalized.action,
            scope=existing.scope,
            name=existing.name,
            old_value=existing.value,
            new_value=normalized.value,
            changed=bool(changed),
        )

    @staticmethod
    def normalize_input(
        operation_input: SubstitutionVariableOperationInput,
    ) -> SubstitutionVariableOperationInput:
        """Normalize and validate a variable-maintenance command."""
        try:
            action = SubstitutionVariableAction(operation_input.action)
        except ValueError as exc:
            raise SubstitutionVariableError(
                "Substitution variable action must be UPDATE or CREATE."
            ) from exc
        scope = str(operation_input.scope).strip()
        name = str(operation_input.name).strip()
        value = str(operation_input.value).strip()
        SubstitutionVariableService.validate_definition(scope, name, value)
        expected = operation_input.expected_current_value
        if action is SubstitutionVariableAction.UPDATE and expected is None:
            raise SubstitutionVariableError(
                "The current variable value is required for a safe update."
            )
        return SubstitutionVariableOperationInput(
            action=action,
            scope=scope,
            name=name,
            value=value,
            expected_current_value=(
                None if expected is None else str(expected)
            ),
        )

    def _discover_with_client(
        self,
        client: EPMClient,
    ) -> SubstitutionVariableCatalog:
        service = SubstitutionVariableService(
            client,
            logger=self._logger.getChild("service"),
        )
        variables = service.get_all_variables()
        plan_types = service.get_plan_types()
        scopes = {
            variable.scope
            for variable in variables
            if variable.scope.casefold() != "all"
        }
        scopes.update(item.cube_name for item in plan_types)
        return SubstitutionVariableCatalog(
            variables=variables,
            plan_types=plan_types,
            scopes=("ALL", *sorted(scopes, key=str.casefold)),
        )

"""Application use cases for governed Planning user-variable values."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.clients.epm_client import EPMClient
from app.config.settings import Settings
from app.models.user_variable import UserVariableDefinition, UserVariableValue
from app.services.user_variable_service import UserVariableService


@dataclass(frozen=True, slots=True)
class UserVariableOperationInput:
    """Approved value change for one Oracle Planning user."""

    user_name: str
    name: str
    dimension: str
    member: str
    expected_current_member: str | None


@dataclass(frozen=True, slots=True)
class UserVariableCatalog:
    """Live definitions and values for one selected Oracle user."""

    user_name: str
    definitions: tuple[UserVariableDefinition, ...]
    values: tuple[UserVariableValue, ...]


@dataclass(frozen=True, slots=True)
class UserVariableChangeResult:
    user_name: str
    name: str
    dimension: str
    old_member: str | None
    new_member: str
    changed: bool


class UserVariableApplicationService:
    """Discover and update user-variable values through one reusable boundary."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: EPMClient | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        if settings is None and client is None:
            raise ValueError("Settings or an authenticated client is required.")
        self._settings = settings
        self._client = client
        self._logger = logger or logging.getLogger(__name__)

    def discover(self, user_name: str) -> UserVariableCatalog:
        normalized_user = str(user_name).strip()
        if self._client is not None:
            return self._discover_with_client(self._client, normalized_user)
        if self._settings is None:
            raise ValueError("Settings are required for live discovery.")
        with EPMClient(self._settings, logger=self._logger.getChild("client")) as client:
            client.authenticate()
            return self._discover_with_client(client, normalized_user)

    def apply(self, command: UserVariableOperationInput) -> UserVariableChangeResult:
        if self._client is None:
            raise ValueError("An authenticated client is required to apply a change.")
        user_name, name, dimension, member = UserVariableService.validate_value(
            command.user_name, command.name, command.dimension, command.member
        )
        service = UserVariableService(self._client, logger=self._logger.getChild("service"))
        current = next(
            (item for item in service.get_values(user_name) if item.name.casefold() == name.casefold()),
            None,
        )
        updated = service.set_value(
            user_name=user_name,
            name=name,
            dimension=dimension,
            member=member,
            expected_current_member=command.expected_current_member,
        )
        return UserVariableChangeResult(
            user_name=updated.user_name,
            name=updated.name,
            dimension=updated.dimension,
            old_member=current.member if current is not None else None,
            new_member=updated.member,
            changed=current is None or current.member != updated.member,
        )

    def _discover_with_client(self, client: EPMClient, user_name: str) -> UserVariableCatalog:
        service = UserVariableService(client, logger=self._logger.getChild("service"))
        values = service.get_values(user_name)
        definitions = service.get_definitions()
        return UserVariableCatalog(
            user_name=user_name,
            definitions=definitions,
            values=values,
        )

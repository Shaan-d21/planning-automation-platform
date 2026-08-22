"""Oracle Planning REST access for user-variable definitions and values."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from urllib.parse import quote

from app.clients.epm_client import EPMClient
from app.models.user_variable import UserVariableDefinition, UserVariableValue
from app.utils.exceptions import APIRequestError, UserVariableError


class UserVariableService:
    """Read definitions and safely set one user's selected member value."""

    def __init__(self, client: EPMClient, *, logger: logging.Logger | None = None) -> None:
        self._client = client
        self._logger = logger or logging.getLogger(__name__)
        application = quote(client.application_name, safe="")
        root = f"{client.planning_api_root}/applications/{application}"
        self._values_endpoint = f"{root}/uservariablevalues"
        self._definitions_endpoint = f"{root}/uservariables"

    def get_values(self, user_name: str | None = None) -> tuple[UserVariableValue, ...]:
        """Return visible values, optionally limited to one exact Oracle user."""
        response = self._client.get(
            self._values_endpoint,
            params={"offset": 0, "limit": -1},
        )
        items = self._response_items(response, "user-variable values")
        requested = str(user_name or "").strip().casefold()
        values = (
            UserVariableValue.from_response(item)
            for item in items
            if isinstance(item, Mapping)
        )
        return tuple(
            sorted(
                (item for item in values if not requested or item.user_name.casefold() == requested),
                key=lambda item: (item.name.casefold(), item.user_name.casefold()),
            )
        )

    def get_definitions(self) -> tuple[UserVariableDefinition, ...]:
        """Return current definitions when supported by the Planning version."""
        try:
            response = self._client.get(
                self._definitions_endpoint,
                params={"offset": 0, "limit": -1},
            )
        except APIRequestError as exc:
            if exc.status_code not in {400, 404, 405}:
                raise
            self._logger.warning(
                "Planning user-variable definition discovery is unavailable; "
                "deriving definitions from assigned values."
            )
            return self._definitions_from_values(self.get_values())
        items = self._response_items(response, "user-variable definitions")
        definitions = {
            (definition.name.casefold(), definition.dimension.casefold()): definition
            for item in items
            if isinstance(item, Mapping)
            for definition in (UserVariableDefinition.from_response(item),)
        }
        return tuple(sorted(definitions.values(), key=lambda item: item.name.casefold()))

    def set_value(
        self,
        *,
        user_name: str,
        name: str,
        dimension: str,
        member: str,
        expected_current_member: str | None,
    ) -> UserVariableValue:
        """Set and reread one value, rejecting a stale current assignment."""
        user_name, name, dimension, member = self.validate_value(
            user_name, name, dimension, member
        )
        current = self.get_values(user_name)
        existing = next((item for item in current if item.name.casefold() == name.casefold()), None)
        actual = existing.member if existing is not None else None
        if actual != expected_current_member:
            raise UserVariableError(
                f"User variable '{name}' for '{user_name}' changed after it was reviewed. "
                "Refresh the live values and try again."
            )
        self._logger.info(
            "Updating user variable: user='%s', name='%s', dimension='%s'.",
            user_name,
            name,
            dimension,
        )
        self._client.post(
            self._values_endpoint,
            payload={
                "items": [
                    {
                        "userName": user_name,
                        "name": name,
                        "dimension": dimension,
                        "member": member,
                    }
                ]
            },
        )
        refreshed = next(
            (
                item
                for item in self.get_values(user_name)
                if item.name.casefold() == name.casefold()
            ),
            None,
        )
        if refreshed is None or refreshed.member != member:
            raise UserVariableError(
                f"Oracle did not confirm user variable '{name}' as '{member}' for '{user_name}'."
            )
        return refreshed

    @staticmethod
    def validate_value(
        user_name: str, name: str, dimension: str, member: str
    ) -> tuple[str, str, str, str]:
        values = tuple(str(value).strip() for value in (user_name, name, dimension, member))
        labels = ("Oracle user name", "User variable name", "Dimension", "Member")
        for label, value in zip(labels, values, strict=True):
            if not value:
                raise UserVariableError(f"{label} is required.")
            if len(value) > 255:
                raise UserVariableError(f"{label} cannot exceed 255 characters.")
        return values

    @staticmethod
    def _definitions_from_values(
        values: tuple[UserVariableValue, ...],
    ) -> tuple[UserVariableDefinition, ...]:
        definitions = {
            (item.name.casefold(), item.dimension.casefold()): UserVariableDefinition(
                name=item.name, dimension=item.dimension
            )
            for item in values
        }
        return tuple(sorted(definitions.values(), key=lambda item: item.name.casefold()))

    @staticmethod
    def _response_items(response: object, label: str) -> list[object]:
        if not isinstance(response, Mapping) or not isinstance(response.get("items"), list):
            raise APIRequestError(f"Oracle {label} response did not contain items.")
        return response["items"]

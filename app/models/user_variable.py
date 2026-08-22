"""Oracle Planning user-variable definitions and assigned values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.utils.exceptions import APIRequestError


@dataclass(frozen=True, slots=True)
class UserVariableDefinition:
    """One application user-variable definition exposed by Planning."""

    name: str
    dimension: str

    @classmethod
    def from_response(cls, item: Mapping[str, object]) -> UserVariableDefinition:
        name = str(item.get("name") or item.get("variableName") or "").strip()
        dimension = str(item.get("dimension") or item.get("dimensionName") or "").strip()
        if not name or not dimension:
            raise APIRequestError(
                "Oracle returned a user-variable definition without a name or dimension."
            )
        return cls(name=name, dimension=dimension)


@dataclass(frozen=True, slots=True)
class UserVariableValue:
    """One user's current member assignment for a Planning user variable."""

    user_name: str
    name: str
    dimension: str
    member: str

    @property
    def key(self) -> tuple[str, str]:
        return self.user_name.casefold(), self.name.casefold()

    @classmethod
    def from_response(cls, item: Mapping[str, object]) -> UserVariableValue:
        user_name = str(item.get("userName") or item.get("username") or "").strip()
        name = str(item.get("name") or item.get("variableName") or "").strip()
        dimension = str(item.get("dimension") or item.get("dimensionName") or "").strip()
        member = str(item.get("member") or item.get("memberName") or "").strip()
        if not user_name or not name or not dimension:
            raise APIRequestError(
                "Oracle returned an incomplete user-variable value."
            )
        return cls(
            user_name=user_name,
            name=name,
            dimension=dimension,
            member=member,
        )

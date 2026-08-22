"""Typed models for Planning plan types and substitution variables."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.utils.exceptions import SubstitutionVariableError


@dataclass(frozen=True, slots=True)
class PlanType:
    """One Planning cube returned by the Get Plan Types API."""

    name: str
    cube_name: str
    identifier: int
    cube_type: int
    dimension_count: int

    @classmethod
    def from_response(cls, response: Mapping[str, Any]) -> PlanType:
        """Create a plan type from an Oracle response item."""
        name = str(response.get("planTypeName", "")).strip()
        cube_name = str(response.get("cubeName", "")).strip() or name
        if not name or not cube_name:
            raise SubstitutionVariableError(
                "Oracle returned a plan type without a name."
            )
        try:
            return cls(
                name=name,
                cube_name=cube_name,
                identifier=int(response.get("planType", 0)),
                cube_type=int(response.get("cubeType", 0)),
                dimension_count=int(response.get("numDimensions", 0)),
            )
        except (TypeError, ValueError) as exc:
            raise SubstitutionVariableError(
                f"Oracle returned invalid details for plan type '{name}'."
            ) from exc


@dataclass(frozen=True, slots=True)
class SubstitutionVariable:
    """One application- or cube-scoped substitution variable."""

    name: str
    value: str
    scope: str

    @classmethod
    def from_response(
        cls,
        response: Mapping[str, Any],
    ) -> SubstitutionVariable:
        """Create a substitution variable from an Oracle response item."""
        name = str(response.get("name", "")).strip()
        scope = str(response.get("planType", "")).strip()
        if not name or not scope:
            raise SubstitutionVariableError(
                "Oracle returned an invalid substitution variable."
            )
        value = response.get("value")
        return cls(
            name=name,
            value="" if value is None else str(value),
            scope=scope,
        )

    @property
    def key(self) -> tuple[str, str]:
        """Return a case-insensitive identity key."""
        return self.scope.casefold(), self.name.casefold()


@dataclass(frozen=True, slots=True)
class SubstitutionVariableUpdate:
    """Approved change to one existing substitution variable."""

    name: str
    scope: str
    old_value: str
    new_value: str

    @property
    def is_changed(self) -> bool:
        """Return whether the requested value differs from Oracle."""
        return self.old_value != self.new_value


@dataclass(frozen=True, slots=True)
class RequestedSubstitutionVariableUpdate:
    """User-selected variable update protected by its observed value."""

    scope: str
    name: str
    expected_current_value: str
    new_value: str

"""Typed read models for Oracle EPM environment discovery."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.utils.exceptions import APIRequestError


@dataclass(frozen=True, slots=True)
class ApplicationInfo:
    """Safe metadata returned for one Oracle EPM application."""

    name: str
    product_type: str | None = None
    application_type: str | None = None
    storage: str | None = None
    admin_mode: bool | None = None
    hybrid: bool | None = None
    unicode: bool | None = None
    theme: str | None = None

    @classmethod
    def from_response(cls, response: Mapping[str, Any]) -> ApplicationInfo:
        """Normalize an Oracle application response."""
        name = str(response.get("name", "")).strip()
        if not name:
            raise APIRequestError(
                "Oracle Planning returned application metadata without a "
                "name."
            )
        return cls(
            name=name,
            product_type=_optional_text(response.get("type")),
            application_type=_optional_text(response.get("appType")),
            storage=_optional_text(response.get("appStorage")),
            admin_mode=_optional_bool(response.get("adminMode")),
            hybrid=_optional_bool(response.get("hybrid")),
            unicode=_optional_bool(response.get("unicode")),
            theme=_optional_text(response.get("theme")),
        )


@dataclass(frozen=True, slots=True)
class DimensionInfo:
    """One dimension returned for a Planning plan type."""

    name: str
    dimension_type: str | None = None

    @classmethod
    def from_response(cls, response: Mapping[str, Any]) -> DimensionInfo:
        name = str(response.get("name", "")).strip()
        if not name:
            raise APIRequestError(
                "Oracle Planning returned dimension metadata without a name."
            )
        return cls(
            name=name,
            dimension_type=_optional_text(response.get("dimType")),
        )


@dataclass(frozen=True, slots=True)
class MemberInfo:
    """One selectable member from a Planning dimension hierarchy."""

    name: str
    alias: str | None = None
    path: str | None = None
    parent_name: str | None = None
    has_children: bool = False


@dataclass(frozen=True, slots=True)
class PlanTypeInfo:
    """One Planning plan type and its discoverable dimensions."""

    name: str
    cube_name: str
    identifier: int | None = None
    cube_type: int | None = None
    dimension_count: int | None = None
    dimensions: tuple[DimensionInfo, ...] = ()

    @classmethod
    def from_response(cls, response: Mapping[str, Any]) -> PlanTypeInfo:
        name = str(
            response.get("planTypeName")
            or response.get("cubeName")
            or ""
        ).strip()
        if not name:
            raise APIRequestError(
                "Oracle Planning returned plan type metadata without a name."
            )
        return cls(
            name=name,
            cube_name=str(response.get("cubeName") or name).strip(),
            identifier=_optional_int(response.get("planType")),
            cube_type=_optional_int(response.get("cubeType")),
            dimension_count=_optional_int(response.get("numDimensions")),
        )


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().casefold()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    return None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

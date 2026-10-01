"""Validate Planning variable values against live dimension metadata."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, timedelta

from app.clients.epm_client import EPMClient
from app.models.environment import DimensionInfo, MemberInfo, PlanTypeInfo
from app.models.planning_metadata import VerifiedPlanningMetadataSnapshot
from app.services.application_service import ApplicationService
from app.utils.exceptions import AccessControlError, AuthenticationError, EPMError


class VariableValueValidationError(EPMError):
    """Raised when a proposed value is incompatible or cannot be proven safe."""


class VariableValueValidationService:
    """Apply type and live-member checks before a variable mutation.

    Oracle substitution variables do not expose a declared value type. For
    those variables, a dimension is inferred only from strong name semantics
    or from the current value's exact live-member identity. Unclassified text
    variables remain valid text; numeric, boolean, and ISO-date variables must
    retain their scalar type.
    """

    _DIMENSION_ALIASES = {
        "scenario": {"scenario", "scen"},
        "year": {"year", "yr", "fiscalyear"},
        "period": {"period", "month", "mth", "mnth"},
        "version": {"version", "ver"},
        "currency": {"currency", "curr"},
        "entity": {"entity", "ent"},
        "account": {"account", "acct"},
        "product": {"product", "prod"},
    }
    _BOOLEAN_VALUES = frozenset({"true", "false", "yes", "no", "on", "off"})
    _NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
    _ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    _PERIOD_VALUES = frozenset(
        {
            "jan", "january", "feb", "february", "mar", "march",
            "apr", "april", "may", "jun", "june", "jul", "july",
            "aug", "august", "sep", "sept", "september", "oct",
            "october", "nov", "november", "dec", "december",
        }
    )
    _SCENARIO_VALUES = frozenset(
        {"actual", "actuals", "forecast", "budget"}
    )
    _YEAR_VALUE = re.compile(r"^fy\s*\d{2,4}$", re.IGNORECASE)

    def __init__(
        self,
        client: EPMClient,
        *,
        application_service: ApplicationService | None = None,
        metadata_fallback: VerifiedPlanningMetadataSnapshot | None = None,
        expected_environment_key: str | None = None,
        fallback_max_age: timedelta = timedelta(hours=24),
    ) -> None:
        self._application = application_service or ApplicationService(client)
        self._plan_types: tuple[PlanTypeInfo, ...] | None = None
        self._members: dict[tuple[str, str], tuple[MemberInfo, ...]] = {}
        self._metadata_fallback = metadata_fallback
        self._expected_environment_key = str(
            expected_environment_key or ""
        ).strip()
        self._fallback_max_age = fallback_max_age

    def validate_user_variable(
        self,
        *,
        variable_name: str,
        dimension: str,
        member: str,
    ) -> str:
        """Require an exact live member from the variable's declared dimension."""
        canonical = self._canonical_dimensions(dimension)
        if not canonical:
            raise VariableValueValidationError(
                f"Oracle did not expose dimension '{dimension}' for user "
                f"variable '{variable_name}'. No change was submitted."
            )
        self._require_member(
            member,
            canonical,
            label=f"user variable '{variable_name}'",
        )
        return member

    @classmethod
    def validate_user_variable_without_live_metadata(
        cls,
        *,
        variable_name: str,
        dimension: str,
        member: str,
    ) -> str:
        """Reject only an obvious cross-type value without querying members.

        Older Planning versions cannot always list dimension members. The
        declared user-variable dimension remains authoritative, while unknown
        member text is allowed through to Oracle for final validation.
        """
        proposed_type = cls._well_known_member_type(member)
        expected_types = cls._dimension_types(dimension)
        if proposed_type and expected_types and proposed_type not in expected_types:
            expected = ", ".join(sorted(expected_types))
            raise VariableValueValidationError(
                f"User variable '{variable_name}' belongs to the {expected} "
                f"dimension, but '{member}' is clearly a {proposed_type} value. "
                "No change was submitted."
            )
        return member

    def validate_substitution_variable(
        self,
        *,
        variable_name: str,
        current_value: str | None,
        proposed_value: str,
        scope: str = "ALL",
    ) -> str:
        """Reject cross-dimension or incompatible scalar replacement values."""
        inferred = self._dimensions_from_name(variable_name, scope=scope)
        current_kind = self._scalar_kind(current_value or "")
        proposed_kind = self._scalar_kind(proposed_value)
        if not inferred and current_value and current_kind != "text":
            if proposed_kind != current_kind:
                raise VariableValueValidationError(
                    f"Substitution variable '{variable_name}' currently contains "
                    f"a {current_kind} value. '{proposed_value}' is not a compatible "
                    f"{current_kind} value. No change was submitted."
                )
            return proposed_value
        if not inferred and current_value:
            inferred = self._dimensions_containing_member(
                current_value,
                scope=scope,
            )
        if inferred:
            self._require_member(
                proposed_value,
                inferred,
                label=f"substitution variable '{variable_name}'",
            )
            return proposed_value

        if current_kind != "text" and proposed_kind != current_kind:
            raise VariableValueValidationError(
                f"Substitution variable '{variable_name}' currently contains "
                f"a {current_kind} value. '{proposed_value}' is not a compatible "
                f"{current_kind} value. No change was submitted."
            )
        return proposed_value

    @classmethod
    def validate_substitution_variable_without_live_metadata(
        cls,
        *,
        variable_name: str,
        current_value: str | None,
        proposed_value: str,
        scope: str = "ALL",
    ) -> str:
        """Validate strong local type facts while allowing unlisted members.

        The scope is accepted for interface parity and future policy use. It
        is deliberately not resolved through a live plan-type endpoint.
        """
        del scope
        current_kind = cls._scalar_kind(current_value or "")
        proposed_kind = cls._scalar_kind(proposed_value)
        if current_value and current_kind != "text" and proposed_kind != current_kind:
            raise VariableValueValidationError(
                f"Substitution variable '{variable_name}' currently contains "
                f"a {current_kind} value. '{proposed_value}' is not a compatible "
                f"{current_kind} value. No change was submitted."
            )
        expected_types = cls._dimension_types(variable_name)
        current_type = cls._well_known_member_type(current_value or "")
        proposed_type = cls._well_known_member_type(proposed_value)
        expected_type = (
            next(iter(expected_types))
            if len(expected_types) == 1
            else current_type
        )
        if expected_type and proposed_type and expected_type != proposed_type:
            raise VariableValueValidationError(
                f"Substitution variable '{variable_name}' expects a "
                f"{expected_type} value, but '{proposed_value}' is clearly a "
                f"{proposed_type} value. No change was submitted."
            )
        return proposed_value

    def _plan_type_catalog(self) -> tuple[PlanTypeInfo, ...]:
        if self._plan_types is None:
            try:
                self._plan_types = self._application.get_plan_types(
                    include_dimensions=True
                )
            except EPMError as exc:
                fallback = self._usable_fallback(exc)
                if fallback is not None:
                    self._plan_types = fallback.plan_types
                    return self._plan_types
                raise VariableValueValidationError(
                    "Oracle dimension metadata is unavailable, so the platform "
                    "cannot prove that this variable value belongs to the correct "
                    "dimension. No change was submitted."
                ) from exc
        return self._plan_types

    def _canonical_dimensions(
        self, requested: str
    ) -> tuple[tuple[str, str], ...]:
        key = requested.strip().casefold()
        result: list[tuple[str, str]] = []
        for plan_type in self._plan_type_catalog():
            for dimension in plan_type.dimensions:
                if dimension.name.casefold() == key:
                    result.append((plan_type.name, dimension.name))
        return tuple(result)

    def _dimensions_from_name(
        self,
        variable_name: str,
        *,
        scope: str,
    ) -> tuple[tuple[str, str], ...]:
        tokens = set(self._name_tokens(variable_name))
        expected_types = {
            dimension_type
            for dimension_type, aliases in self._DIMENSION_ALIASES.items()
            if tokens & aliases
        }
        if not expected_types:
            return ()
        result: list[tuple[str, str]] = []
        for plan_type in self._scoped_plan_types(scope):
            for dimension in plan_type.dimensions:
                dimension_keys = {
                    dimension.name.casefold(),
                    str(dimension.dimension_type or "").casefold(),
                }
                if dimension_keys & expected_types:
                    result.append((plan_type.name, dimension.name))
        if not result:
            expected = ", ".join(sorted(expected_types))
            raise VariableValueValidationError(
                f"Substitution variable '{variable_name}' appears to require "
                f"the {expected} dimension, but Oracle did not expose that "
                "dimension. No change was submitted."
            )
        return tuple(result)

    def _dimensions_containing_member(
        self,
        value: str,
        *,
        scope: str,
    ) -> tuple[tuple[str, str], ...]:
        result: list[tuple[str, str]] = []
        for plan_type in self._scoped_plan_types(scope):
            for dimension in plan_type.dimensions:
                if self._member_exists(plan_type.name, dimension.name, value):
                    result.append((plan_type.name, dimension.name))
        distinct_dimensions = {
            dimension.casefold() for _cube, dimension in result
        }
        if len(distinct_dimensions) > 1:
            dimensions = ", ".join(
                sorted({dimension for _cube, dimension in result}, key=str.casefold)
            )
            raise VariableValueValidationError(
                f"Current value '{value}' exists in multiple dimensions "
                f"({dimensions}), so the platform cannot safely infer this "
                "substitution variable's value type. No change was submitted."
            )
        return tuple(result)

    def _scoped_plan_types(self, scope: str) -> tuple[PlanTypeInfo, ...]:
        plan_types = self._plan_type_catalog()
        normalized = str(scope or "ALL").strip().casefold()
        if normalized == "all":
            return plan_types
        matches = tuple(
            plan_type
            for plan_type in plan_types
            if normalized
            in {plan_type.name.casefold(), plan_type.cube_name.casefold()}
        )
        if not matches:
            raise VariableValueValidationError(
                f"Oracle did not expose cube scope '{scope}', so the proposed "
                "substitution-variable value cannot be validated. No change "
                "was submitted."
            )
        return matches

    def _require_member(
        self,
        member: str,
        dimensions: Iterable[tuple[str, str]],
        *,
        label: str,
    ) -> None:
        checked = tuple(dict.fromkeys(dimensions))
        if any(self._member_exists(cube, dimension, member) for cube, dimension in checked):
            return
        dimension_names = ", ".join(
            sorted({dimension for _cube, dimension in checked}, key=str.casefold)
        )
        raise VariableValueValidationError(
            f"'{member}' is not an exact live member of dimension "
            f"'{dimension_names}' required by {label}. No change was submitted."
        )

    def _member_exists(self, cube: str, dimension: str, value: str) -> bool:
        key = (cube.casefold(), dimension.casefold())
        if key not in self._members:
            try:
                self._members[key] = self._application.get_dimension_members(
                    cube, dimension
                )
            except EPMError as exc:
                fallback = self._usable_fallback(exc)
                fallback_members = (
                    fallback.members_for(cube, dimension)
                    if fallback is not None else None
                )
                if fallback_members is not None:
                    self._members[key] = fallback_members
                else:
                    raise VariableValueValidationError(
                        f"Oracle members for dimension '{dimension}' are unavailable, "
                        "so the proposed variable value cannot be validated. No "
                        "change was submitted."
                    ) from exc
        requested = value.strip().casefold()
        # Submit canonical member names only. An alias can be ambiguous and
        # Oracle's variable endpoints expect the stored member identifier.
        return any(requested == item.name.casefold() for item in self._members[key])

    def _usable_fallback(
        self,
        live_error: EPMError,
    ) -> VerifiedPlanningMetadataSnapshot | None:
        """Return a fresh verified snapshot only for compatibility failures."""
        if isinstance(live_error, (AuthenticationError, AccessControlError)):
            return None
        snapshot = self._metadata_fallback
        if snapshot is None or not snapshot.is_fresh(max_age=self._fallback_max_age):
            return None
        if (
            self._expected_environment_key
            and snapshot.environment_key != self._expected_environment_key
        ):
            return None
        return snapshot

    @classmethod
    def _name_tokens(cls, value: str) -> tuple[str, ...]:
        expanded = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
        compact = re.sub(r"[^a-z0-9]+", "", expanded.casefold())
        tokens = tuple(re.findall(r"[a-z]+", expanded.casefold()))
        aliases = {
            alias
            for values in cls._DIMENSION_ALIASES.values()
            for alias in values
            if alias
            and (
                compact == alias
                or compact.endswith(alias)
                or (len(alias) >= 4 and compact.startswith(alias))
            )
        }
        return (*tokens, *sorted(aliases))

    @classmethod
    def _dimension_types(cls, value: str) -> set[str]:
        tokens = set(cls._name_tokens(value))
        return {
            dimension_type
            for dimension_type, aliases in cls._DIMENSION_ALIASES.items()
            if tokens & aliases
        }

    @classmethod
    def _well_known_member_type(cls, value: str) -> str | None:
        normalized = str(value or "").strip().casefold()
        if cls._YEAR_VALUE.fullmatch(normalized):
            return "year"
        if normalized in cls._PERIOD_VALUES:
            return "period"
        if normalized in cls._SCENARIO_VALUES:
            return "scenario"
        return None

    @classmethod
    def _scalar_kind(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized in cls._BOOLEAN_VALUES:
            return "boolean"
        if cls._NUMBER.fullmatch(normalized):
            return "number"
        if cls._ISO_DATE.fullmatch(normalized):
            try:
                date.fromisoformat(normalized)
            except ValueError:
                return "text"
            return "date"
        return "text"

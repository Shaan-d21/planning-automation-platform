from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.planning_metadata import VerifiedPlanningMetadataSnapshot
from app.models.environment import DimensionInfo, MemberInfo, PlanTypeInfo
from app.services.variable_value_validation_service import (
    VariableValueValidationError,
    VariableValueValidationService,
)
from app.utils.exceptions import AuthenticationError, EPMConnectionError


def _snapshot(*, age: timedelta = timedelta(minutes=5)):
    return VerifiedPlanningMetadataSnapshot(
        environment_key="env-1",
        verified_at=datetime.now(UTC) - age,
        plan_types=(
            PlanTypeInfo(
                name="Plan1",
                cube_name="Plan1",
                dimensions=(
                    DimensionInfo(name="Scenario", dimension_type="Scenario"),
                ),
            ),
        ),
        members={
            ("Plan1", "Scenario"): (
                MemberInfo(name="Actual"),
                MemberInfo(name="Forecast"),
            )
        },
    )


class UnsupportedMetadata:
    def get_plan_types(self, *, include_dimensions):
        raise EPMConnectionError("This Planning version has no endpoint")

    def get_dimension_members(self, cube, dimension):
        raise EPMConnectionError("This Planning version has no endpoint")


class RejectedCredentials(UnsupportedMetadata):
    def get_plan_types(self, *, include_dimensions):
        raise AuthenticationError("invalid credentials")


def test_fresh_verified_snapshot_supports_old_planning_version() -> None:
    service = VariableValueValidationService(
        SimpleNamespace(),
        application_service=UnsupportedMetadata(),
        metadata_fallback=_snapshot(),
        expected_environment_key="env-1",
    )

    assert service.validate_substitution_variable(
        variable_name="CurrentScenario",
        current_value="Actual",
        proposed_value="Forecast",
        scope="Plan1",
    ) == "Forecast"


def test_stale_snapshot_fails_closed() -> None:
    service = VariableValueValidationService(
        SimpleNamespace(),
        application_service=UnsupportedMetadata(),
        metadata_fallback=_snapshot(age=timedelta(days=2)),
        expected_environment_key="env-1",
    )

    with pytest.raises(VariableValueValidationError, match="metadata is unavailable"):
        service.validate_substitution_variable(
            variable_name="CurrentScenario",
            current_value="Actual",
            proposed_value="Forecast",
            scope="Plan1",
        )


def test_authentication_failure_never_uses_snapshot_fallback() -> None:
    service = VariableValueValidationService(
        SimpleNamespace(),
        application_service=RejectedCredentials(),
        metadata_fallback=_snapshot(),
        expected_environment_key="env-1",
    )

    with pytest.raises(VariableValueValidationError, match="metadata is unavailable"):
        service.validate_substitution_variable(
            variable_name="CurrentScenario",
            current_value="Actual",
            proposed_value="Forecast",
            scope="Plan1",
        )

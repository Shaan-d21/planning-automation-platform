"""Tests for live type validation of Planning variable values."""

from unittest.mock import Mock

import pytest

from app.models.environment import DimensionInfo, MemberInfo, PlanTypeInfo
from app.services.variable_value_validation_service import (
    VariableValueValidationError,
    VariableValueValidationService,
)


def _service() -> tuple[VariableValueValidationService, Mock]:
    application = Mock()
    application.get_plan_types.return_value = (
        PlanTypeInfo(
            name="Plan1",
            cube_name="Plan1",
            dimensions=(
                DimensionInfo("Scenario", "scenario"),
                DimensionInfo("Year", "year"),
                DimensionInfo("Period", "period"),
                DimensionInfo("Entity", "entity"),
            ),
        ),
    )
    members = {
        "Scenario": (MemberInfo("Actual"), MemberInfo("Forecast")),
        "Year": (MemberInfo("FY27"), MemberInfo("FY28")),
        "Period": (MemberInfo("Jan"), MemberInfo("Feb")),
        "Entity": (MemberInfo("Sales East"), MemberInfo("Sales West")),
    }
    application.get_dimension_members.side_effect = (
        lambda _cube, dimension: members[dimension]
    )
    return (
        VariableValueValidationService(
            Mock(), application_service=application
        ),
        application,
    )


def test_user_variable_requires_member_from_its_declared_dimension() -> None:
    service, _ = _service()

    assert service.validate_user_variable(
        variable_name="MyEntity",
        dimension="Entity",
        member="Sales West",
    ) == "Sales West"

    with pytest.raises(
        VariableValueValidationError,
        match="not an exact live member of dimension 'Entity'",
    ):
        service.validate_user_variable(
            variable_name="MyEntity",
            dimension="Entity",
            member="FY28",
        )


def test_scenario_substitution_variable_rejects_year_member() -> None:
    service, _ = _service()

    with pytest.raises(
        VariableValueValidationError,
        match="dimension 'Scenario'",
    ):
        service.validate_substitution_variable(
            variable_name="CurrentScenario",
            current_value="Actual",
            proposed_value="FY28",
        )


def test_year_and_period_variable_names_resolve_live_member_type() -> None:
    service, application = _service()

    assert service.validate_substitution_variable(
        variable_name="CurYr",
        current_value="FY27",
        proposed_value="FY28",
    ) == "FY28"
    assert service.validate_substitution_variable(
        variable_name="StartMonth",
        current_value="Jan",
        proposed_value="Feb",
    ) == "Feb"
    assert application.get_plan_types.call_count == 1


def test_untyped_numeric_variable_cannot_change_to_text() -> None:
    service, application = _service()

    with pytest.raises(
        VariableValueValidationError,
        match="currently contains a number value",
    ):
        service.validate_substitution_variable(
            variable_name="BatchSize",
            current_value="10",
            proposed_value="Jan",
        )

    application.get_plan_types.assert_not_called()


def test_member_type_can_be_inferred_from_current_live_value() -> None:
    service, _ = _service()

    with pytest.raises(
        VariableValueValidationError,
        match="dimension 'Entity'",
    ):
        service.validate_substitution_variable(
            variable_name="ReportingSelection",
            current_value="Sales East",
            proposed_value="Forecast",
        )


def test_untyped_variable_fails_closed_when_current_member_is_ambiguous() -> None:
    service, application = _service()
    members = {
        "Scenario": (MemberInfo("Actual"), MemberInfo("Forecast")),
        "Year": (MemberInfo("FY27"), MemberInfo("FY28")),
        "Period": (MemberInfo("Jan"), MemberInfo("Feb")),
        "Entity": (MemberInfo("Actual"), MemberInfo("Sales West")),
    }
    application.get_dimension_members.side_effect = (
        lambda _cube, dimension: members[dimension]
    )

    with pytest.raises(
        VariableValueValidationError,
        match="exists in multiple dimensions",
    ):
        service.validate_substitution_variable(
            variable_name="ReportingSelection",
            current_value="Actual",
            proposed_value="Forecast",
        )


def test_cube_scoped_variable_does_not_accept_member_from_another_cube() -> None:
    service, application = _service()
    application.get_plan_types.return_value = (
        PlanTypeInfo(
            name="Plan1",
            cube_name="Plan1",
            dimensions=(DimensionInfo("Scenario", "scenario"),),
        ),
        PlanTypeInfo(
            name="Plan2",
            cube_name="Plan2",
            dimensions=(DimensionInfo("Scenario", "scenario"),),
        ),
    )
    application.get_dimension_members.side_effect = (
        lambda cube, _dimension: (
            (MemberInfo("Actual"), MemberInfo("Forecast"))
            if cube == "Plan1"
            else (MemberInfo("Working"),)
        )
    )

    with pytest.raises(
        VariableValueValidationError,
        match="dimension 'Scenario'",
    ):
        service.validate_substitution_variable(
            variable_name="CurrentScenario",
            current_value="Actual",
            proposed_value="Working",
            scope="Plan1",
        )


def test_offline_substitution_validation_allows_unknown_text_without_live_calls() -> None:
    service, application = _service()

    assert service.validate_substitution_variable_without_live_metadata(
        variable_name="CustomSelection",
        current_value="Old Custom Member",
        proposed_value="New Custom Member",
    ) == "New Custom Member"

    application.get_plan_types.assert_not_called()
    application.get_dimension_members.assert_not_called()


def test_offline_variable_validation_rejects_only_obvious_cross_type_value() -> None:
    service, application = _service()

    with pytest.raises(VariableValueValidationError, match="expects a scenario"):
        service.validate_substitution_variable_without_live_metadata(
            variable_name="CurrentScenario",
            current_value="Actual",
            proposed_value="FY28",
        )
    with pytest.raises(VariableValueValidationError, match="clearly a year value"):
        service.validate_user_variable_without_live_metadata(
            variable_name="MyScenario",
            dimension="Scenario",
            member="FY28",
        )

    application.get_plan_types.assert_not_called()
    application.get_dimension_members.assert_not_called()

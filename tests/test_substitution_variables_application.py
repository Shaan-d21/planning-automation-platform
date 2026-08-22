"""Tests for governed substitution-variable application use cases."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from app.application.substitution_variables import (
    SubstitutionVariableAction,
    SubstitutionVariableApplicationService,
    SubstitutionVariableOperationInput,
)
from app.models.substitution_variable import PlanType, SubstitutionVariable
from app.utils.exceptions import SubstitutionVariableError


def _client() -> Mock:
    client = Mock()
    client.application_name = "Vision"
    client.planning_api_root = "https://example.oraclecloud.com/rest/v3"
    return client


def test_discover_combines_application_and_cube_scopes() -> None:
    service = SubstitutionVariableApplicationService(client=_client())
    variable_service = Mock()
    variable_service.get_all_variables.return_value = (
        SubstitutionVariable("CurYr", "FY26", "ALL"),
        SubstitutionVariable("CurPeriod", "Jan", "Plan1"),
    )
    variable_service.get_plan_types.return_value = (
        PlanType("Workforce", "Workforce", 2, 1, 10),
    )

    from unittest.mock import patch

    with patch(
        "app.application.substitution_variables."
        "SubstitutionVariableService",
        return_value=variable_service,
    ):
        catalog = service.discover()

    assert catalog.scopes == ("ALL", "Plan1", "Workforce")
    assert len(catalog.variables) == 2


def test_update_stops_when_current_value_changed_after_review() -> None:
    service = SubstitutionVariableApplicationService(client=_client())
    variable_service = Mock()
    variable_service.get_all_variables.return_value = (
        SubstitutionVariable("CurYr", "FY27", "ALL"),
    )

    from unittest.mock import patch

    with patch(
        "app.application.substitution_variables."
        "SubstitutionVariableService",
        return_value=variable_service,
    ):
        with pytest.raises(
            SubstitutionVariableError,
            match="changed after this page was loaded",
        ):
            service.apply(
                SubstitutionVariableOperationInput(
                    action=SubstitutionVariableAction.UPDATE,
                    scope="ALL",
                    name="CurYr",
                    value="FY26",
                    expected_current_value="FY25",
                )
            )

    variable_service.apply_updates.assert_not_called()

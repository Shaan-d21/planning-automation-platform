"""Tests for REST substitution-variable discovery and updates."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from app.clients.epm_client import EPMClient
from app.models.substitution_variable import (
    RequestedSubstitutionVariableUpdate,
    SubstitutionVariable,
)
from app.services.substitution_variable_service import (
    SubstitutionVariableService,
)
from app.utils.exceptions import APIRequestError, SubstitutionVariableError


def _client() -> Mock:
    client = Mock(spec=EPMClient)
    client.application_name = "Vision"
    client.planning_api_root = "HyperionPlanning/rest/v3"
    return client


def test_get_all_variables_preserves_all_and_cube_scopes() -> None:
    client = _client()
    client.get.return_value = {
        "items": [
            {"name": "CurYr", "value": "FY25", "planType": "ALL"},
            {"name": "CurYr", "value": "FY26", "planType": "Plan1"},
        ]
    }

    variables = SubstitutionVariableService(client).get_all_variables()

    assert [(item.scope, item.name, item.value) for item in variables] == [
        ("ALL", "CurYr", "FY25"),
        ("Plan1", "CurYr", "FY26"),
    ]


def test_get_plan_types_falls_back_when_endpoint_is_unavailable() -> None:
    client = _client()
    client.get.side_effect = [
        APIRequestError("Not Found", status_code=404),
        {
            "items": [
                {
                    "name": "CurPeriod",
                    "value": "Jan",
                    "planType": "Plan1",
                }
            ]
        },
        {"items": []},
    ]

    plan_types = SubstitutionVariableService(client).get_plan_types()

    assert [item.cube_name for item in plan_types] == ["Plan1"]


def test_get_plan_types_uses_job_definitions_for_on_prem_cubes() -> None:
    client = _client()
    client.is_cloud_environment = False
    client.get.side_effect = [
        APIRequestError("Not Found", status_code=404),
        {
            "items": [
                {"name": "CurPeriod", "value": "Jan", "planType": "ALL"},
            ]
        },
        {
            "items": [
                {"jobName": "Rule 1", "planTypeName": "Plan1"},
                {"jobName": "Rule 2", "planTypeName": "Plan2"},
            ]
        },
    ]

    plan_types = SubstitutionVariableService(client).get_plan_types()

    assert [item.cube_name for item in plan_types] == ["Plan1", "Plan2"]


def test_build_updates_refuses_to_create_undiscovered_variable() -> None:
    service = SubstitutionVariableService(_client())
    current = (
        SubstitutionVariable("CurYr", "FY25", "ALL"),
    )

    with pytest.raises(SubstitutionVariableError, match="not discovered"):
        service.build_updates(
            {("Plan1", "CurYr"): "FY26"},
            current_variables=current,
        )


def test_build_safe_updates_uses_exact_scope_and_observed_value() -> None:
    service = SubstitutionVariableService(_client())
    current = (
        SubstitutionVariable("CurYr", "FY25", "ALL"),
        SubstitutionVariable("CurYr", "FY24", "Plan1"),
    )

    updates = service.build_safe_updates(
        (
            RequestedSubstitutionVariableUpdate(
                scope="Plan1",
                name="CurYr",
                expected_current_value="FY24",
                new_value="FY26",
            ),
        ),
        current_variables=current,
    )

    assert len(updates) == 1
    assert updates[0].scope == "Plan1"
    assert updates[0].old_value == "FY24"
    assert updates[0].new_value == "FY26"


def test_build_safe_updates_rejects_stale_observed_value() -> None:
    service = SubstitutionVariableService(_client())

    with pytest.raises(SubstitutionVariableError, match="changed after"):
        service.build_safe_updates(
            (
                RequestedSubstitutionVariableUpdate(
                    scope="ALL",
                    name="CurYr",
                    expected_current_value="FY24",
                    new_value="FY26",
                ),
            ),
            current_variables=(
                SubstitutionVariable("CurYr", "FY25", "ALL"),
            ),
        )


def test_apply_updates_groups_payload_by_scope() -> None:
    client = _client()
    client.get.return_value = {
        "items": [
            {"name": "CurYr", "value": "FY26", "planType": "ALL"},
            {
                "name": "CurPeriod",
                "value": "Feb",
                "planType": "Plan1",
            },
        ]
    }
    service = SubstitutionVariableService(client)
    current = (
        SubstitutionVariable("CurYr", "FY25", "ALL"),
        SubstitutionVariable("CurPeriod", "Jan", "Plan1"),
    )
    updates = service.build_updates(
        {
            ("ALL", "CurYr"): "FY26",
            ("Plan1", "CurPeriod"): "Feb",
        },
        current_variables=current,
    )

    changed = service.apply_updates(updates)

    assert len(changed) == 2
    assert client.post.call_count == 2
    client.post.assert_any_call(
        "HyperionPlanning/rest/v3/applications/Vision/"
        "substitutionvariables",
        payload={
            "items": [
                {
                    "name": "CurYr",
                    "value": "FY26",
                    "planType": "ALL",
                }
            ]
        },
    )
    client.post.assert_any_call(
        "HyperionPlanning/rest/v3/applications/Vision/plantypes/"
        "Plan1/substitutionvariables",
        payload={
            "items": [
                {
                    "name": "CurPeriod",
                    "value": "Feb",
                    "planType": "Plan1",
                }
            ]
        },
    )


def test_create_variable_is_explicit_and_verified() -> None:
    client = _client()
    client.get.return_value = {
        "items": [
            {
                "name": "ForecastEnd",
                "value": "Mar",
                "planType": "ALL",
            }
        ]
    }

    created = SubstitutionVariableService(client).create_variable(
        "ALL",
        "ForecastEnd",
        "Mar",
        current_variables=(),
    )

    assert created == SubstitutionVariable("ForecastEnd", "Mar", "ALL")
    client.post.assert_called_once_with(
        "HyperionPlanning/rest/v3/applications/Vision/"
        "substitutionvariables",
        payload={
            "items": [
                {
                    "name": "ForecastEnd",
                    "value": "Mar",
                    "planType": "ALL",
                }
            ]
        },
    )


def test_create_variable_rejects_existing_definition() -> None:
    service = SubstitutionVariableService(_client())

    with pytest.raises(SubstitutionVariableError, match="already exists"):
        service.create_variable(
            "ALL",
            "CurYr",
            "FY26",
            current_variables=(
                SubstitutionVariable("CurYr", "FY25", "ALL"),
            ),
        )

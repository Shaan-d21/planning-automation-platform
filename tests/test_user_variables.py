"""Tests for governed Oracle Planning user-variable values."""

from unittest.mock import Mock

import pytest

from app.application.user_variables import (
    UserVariableApplicationService,
    UserVariableOperationInput,
)
from app.services.user_variable_service import UserVariableService
from app.utils.exceptions import APIRequestError, UserVariableError


def _client() -> Mock:
    return Mock(application_name="Vision", planning_api_root="HyperionPlanning/rest/v3")


def test_discovers_definitions_and_values_for_selected_user() -> None:
    client = _client()
    client.get.side_effect = (
        {"items": [
            {"userName": "planner@example.com", "name": "MyEntity", "dimension": "Entity", "member": "Sales East"},
            {"userName": "other@example.com", "name": "MyEntity", "dimension": "Entity", "member": "Sales West"},
        ]},
        {"items": [{"name": "MyEntity", "dimension": "Entity"}]},
    )

    catalog = UserVariableApplicationService(client=client).discover("planner@example.com")

    assert catalog.user_name == "planner@example.com"
    assert [(item.name, item.dimension) for item in catalog.definitions] == [("MyEntity", "Entity")]
    assert [(item.user_name, item.member) for item in catalog.values] == [("planner@example.com", "Sales East")]


def test_definition_discovery_falls_back_for_older_planning_versions() -> None:
    client = _client()
    client.get.side_effect = (
        APIRequestError("Not Found", status_code=404),
        {"items": [{"userName": "planner@example.com", "name": "MyEntity", "dimension": "Entity", "member": "Sales East"}]},
    )

    definitions = UserVariableService(client).get_definitions()

    assert [(item.name, item.dimension) for item in definitions] == [("MyEntity", "Entity")]


def test_apply_posts_oracle_contract_and_verifies_result() -> None:
    client = _client()
    old = {"items": [{"userName": "planner@example.com", "name": "MyEntity", "dimension": "Entity", "member": "Sales East"}]}
    new = {"items": [{"userName": "planner@example.com", "name": "MyEntity", "dimension": "Entity", "member": "Sales West"}]}
    client.get.side_effect = (old, old, new)

    result = UserVariableApplicationService(client=client).apply(
        UserVariableOperationInput(
            user_name="planner@example.com",
            name="MyEntity",
            dimension="Entity",
            member="Sales West",
            expected_current_member="Sales East",
        )
    )

    assert result.old_member == "Sales East"
    assert result.new_member == "Sales West"
    client.post.assert_called_once_with(
        "HyperionPlanning/rest/v3/applications/Vision/uservariablevalues",
        payload={"items": [{"userName": "planner@example.com", "name": "MyEntity", "dimension": "Entity", "member": "Sales West"}]},
    )


def test_stale_assignment_is_rejected_without_post() -> None:
    client = _client()
    client.get.return_value = {"items": [{"userName": "planner@example.com", "name": "MyEntity", "dimension": "Entity", "member": "Sales Central"}]}

    with pytest.raises(UserVariableError, match="changed after it was reviewed"):
        UserVariableApplicationService(client=client).apply(
            UserVariableOperationInput(
                user_name="planner@example.com",
                name="MyEntity",
                dimension="Entity",
                member="Sales West",
                expected_current_member="Sales East",
            )
        )

    client.post.assert_not_called()

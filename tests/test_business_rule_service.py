"""Tests for Oracle Planning Business Rule REST execution."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from app.clients.epm_client import EPMClient
from app.services.business_rule_service import BusinessRuleService
from app.utils.exceptions import BusinessRuleError


@pytest.fixture
def client() -> Mock:
    value = Mock(spec=EPMClient)
    value.application_name = "Vision"
    value.planning_api_root = "HyperionPlanning/rest/v3"
    return value


def test_start_rule_sends_runtime_prompts(client: Mock) -> None:
    client.post.return_value = {
        "jobId": 301,
        "status": -1,
        "descriptiveStatus": "Processing",
    }

    submission = BusinessRuleService(client).start_rule(
        "Calculate Vehicle Revenue",
        runtime_prompts={
            "Scenario": "Plan",
            "Entity": "USA",
        },
    )

    assert submission.job_id == 301
    assert submission.rule_name == "Calculate Vehicle Revenue"
    assert submission.runtime_prompts == (
        ("Scenario", "Plan"),
        ("Entity", "USA"),
    )
    client.post.assert_called_once_with(
        "HyperionPlanning/rest/v3/applications/Vision/jobs",
        payload={
            "jobType": "RULES",
            "jobName": "Calculate Vehicle Revenue",
            "parameters": {
                "Scenario": "Plan",
                "Entity": "USA",
            },
        },
    )


def test_start_rule_omits_parameters_when_defaults_are_used(
    client: Mock,
) -> None:
    client.post.return_value = {"jobId": 302, "status": -1}

    BusinessRuleService(client).start_rule("Calculate Revenue")

    client.post.assert_called_once_with(
        "HyperionPlanning/rest/v3/applications/Vision/jobs",
        payload={
            "jobType": "RULES",
            "jobName": "Calculate Revenue",
        },
    )


def test_start_rule_rejects_immediate_oracle_error(client: Mock) -> None:
    client.post.return_value = {
        "jobId": 303,
        "status": 4,
        "details": "Missing runtime prompt Entity",
    }

    with pytest.raises(
        BusinessRuleError,
        match="Missing runtime prompt Entity",
    ):
        BusinessRuleService(client).start_rule("Calculate Revenue")


@pytest.mark.parametrize(
    ("prompts", "message"),
    [
        ({"": "USA"}, "name cannot be empty"),
        ({"Entity": ""}, "requires a value"),
        ({"Entity=Bad": "USA"}, "cannot contain"),
    ],
)
def test_runtime_prompt_validation(
    client: Mock,
    prompts: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(BusinessRuleError, match=message):
        BusinessRuleService(client).start_rule(
            "Calculate Revenue",
            runtime_prompts=prompts,
        )

    client.post.assert_not_called()

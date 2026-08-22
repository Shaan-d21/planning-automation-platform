"""Tests for Business Rule execution through EPM Automate."""

from __future__ import annotations

from unittest.mock import Mock, call

import pytest

from app.automation.epm_automate_runner import EPMAutomateRunner
from app.models.command import CommandResult
from app.services.epm_automate_business_rule_service import (
    EPMAutomateBusinessRuleService,
)
from app.utils.exceptions import EPMAutomateCommandError


def command_result(
    command: str,
    *,
    return_code: int = 0,
    stdout: str = "Command completed successfully",
    stderr: str = "",
) -> CommandResult:
    return CommandResult(
        command=command,
        return_code=return_code,
        stdout=stdout,
        stderr=stderr,
    )


def make_service(
    runner: Mock,
    password_file,
) -> EPMAutomateBusinessRuleService:
    return EPMAutomateBusinessRuleService(
        runner,
        username="epm.user",
        password_file=password_file,
        base_url="https://example.oraclecloud.com",
    )


def test_run_rule_passes_each_runtime_prompt(tmp_path) -> None:
    password_file = tmp_path / "password.epw"
    runner = Mock(spec=EPMAutomateRunner)
    runner.run.side_effect = [
        command_result("login"),
        command_result("runBusinessRule"),
        command_result("logout"),
    ]

    result = make_service(runner, password_file).run_rule(
        "Calculate Vehicle Revenue",
        runtime_prompts={
            "Scenario": "Plan",
            "Entity": "North America",
        },
    )

    assert result.runtime_prompts == (
        ("Scenario", "Plan"),
        ("Entity", "North America"),
    )
    assert runner.run.call_args_list[1] == call(
        "runBusinessRule",
        "Calculate Vehicle Revenue",
        "Scenario=Plan",
        "Entity=North America",
        check=False,
    )


def test_run_rule_uses_calculation_manager_defaults(tmp_path) -> None:
    password_file = tmp_path / "password.epw"
    runner = Mock(spec=EPMAutomateRunner)
    runner.run.side_effect = [
        command_result("login"),
        command_result("runBusinessRule"),
        command_result("logout"),
    ]

    make_service(runner, password_file).run_rule("Calculate Revenue")

    assert runner.run.call_args_list[1] == call(
        "runBusinessRule",
        "Calculate Revenue",
        check=False,
    )


def test_rule_failure_still_logs_out(tmp_path) -> None:
    password_file = tmp_path / "password.epw"
    runner = Mock(spec=EPMAutomateRunner)
    runner.run.side_effect = [
        command_result("login"),
        command_result(
            "runBusinessRule",
            return_code=1,
            stderr="Rule execution failed",
        ),
        command_result("logout"),
    ]

    with pytest.raises(
        EPMAutomateCommandError,
        match="Rule execution failed",
    ):
        make_service(runner, password_file).run_rule("Calculate Revenue")

    assert runner.run.call_args_list[-1] == call(
        "logout",
        check=False,
    )

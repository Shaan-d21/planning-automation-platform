"""Tests for Pipeline execution through EPM Automate."""

from __future__ import annotations

from unittest.mock import Mock, call

import pytest

from app.automation.epm_automate_runner import EPMAutomateRunner
from app.models.command import CommandResult
from app.services.epm_automate_pipeline_service import (
    EPMAutomatePipelineService,
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
) -> EPMAutomatePipelineService:
    return EPMAutomatePipelineService(
        runner,
        username="epm.user",
        password_file=password_file,
        base_url="https://example.oraclecloud.com",
    )


def test_run_pipeline_passes_all_variables(tmp_path) -> None:
    password_file = tmp_path / "password.epw"
    runner = Mock(spec=EPMAutomateRunner)
    runner.run.side_effect = [
        command_result("login"),
        command_result("runPipeline"),
        command_result("logout"),
    ]

    result = make_service(runner, password_file).run_pipeline(
        "PIPE01",
        variables={
            "STARTPERIOD": "Jan-26",
            "ENDPERIOD": "Mar-26",
            "IMPORTMODE": "Replace",
            "EXPORTMODE": "Merge",
            "SEND_MAIL": "No",
            "ATTACH_LOGS": "N",
        },
    )

    assert result.pipeline_code == "PIPE01"
    assert runner.run.call_args_list[1] == call(
        "runPipeline",
        "PIPE01",
        "STARTPERIOD=Jan-26",
        "ENDPERIOD=Mar-26",
        "IMPORTMODE=Replace",
        "EXPORTMODE=Merge",
        "SEND_MAIL=No",
        "ATTACH_LOGS=N",
        check=False,
    )


def test_pipeline_failure_still_logs_out(tmp_path) -> None:
    password_file = tmp_path / "password.epw"
    runner = Mock(spec=EPMAutomateRunner)
    runner.run.side_effect = [
        command_result("login"),
        command_result(
            "runPipeline",
            return_code=1,
            stderr="Pipeline execution failed",
        ),
        command_result("logout"),
    ]

    with pytest.raises(
        EPMAutomateCommandError,
        match="Pipeline execution failed",
    ):
        make_service(runner, password_file).run_pipeline("PIPE01")

    assert runner.run.call_args_list[-1] == call(
        "logout",
        check=False,
    )

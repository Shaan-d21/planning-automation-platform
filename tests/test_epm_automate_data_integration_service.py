"""Tests for Data Integration execution through EPM Automate."""

from __future__ import annotations

from unittest.mock import Mock, call

import pytest

from app.automation.epm_automate_runner import EPMAutomateRunner
from app.models.command import CommandResult
from app.models.data_integration import DataIntegrationPeriodRange
from app.services.epm_automate_data_integration_service import (
    EPMAutomateDataIntegrationService,
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
) -> EPMAutomateDataIntegrationService:
    return EPMAutomateDataIntegrationService(
        runner,
        username="epm.user",
        password_file=password_file,
        base_url="https://example.oraclecloud.com",
    )


def period_range() -> DataIntegrationPeriodRange:
    return DataIntegrationPeriodRange.from_period_names(
        "Jun-19",
        "Aug-19",
        expected_period_count=3,
    )


def test_run_integration_uploads_and_passes_exact_parameters(
    tmp_path,
) -> None:
    data_file = tmp_path / "Test_Sales_DataLoad_V2.csv"
    data_file.write_text("A,B,C,D,E,1,2,3", encoding="utf-8")
    password_file = tmp_path / "password.epw"
    runner = Mock(spec=EPMAutomateRunner)
    runner.run.side_effect = [
        command_result("login"),
        command_result("uploadFile"),
        command_result("runIntegration"),
        command_result("logout"),
    ]

    result = make_service(runner, password_file).run_integration(
        data_file=data_file,
        inbox_file_name=None,
        integration_name="Test_DataLoad",
        period_range=period_range(),
    )

    assert result.period_name == "{Jun-19}{Aug-19}"
    assert runner.run.call_args_list[2] == call(
        "runIntegration",
        "Test_DataLoad",
        "importMode=Replace",
        "exportMode=Merge",
        "periodName={Jun-19}{Aug-19}",
        "inputFileName=#epminbox/Test_Sales_DataLoad_V2.csv",
        check=False,
    )


def test_run_integration_uses_existing_inbox_file(tmp_path) -> None:
    password_file = tmp_path / "password.epw"
    runner = Mock(spec=EPMAutomateRunner)
    runner.run.side_effect = [
        command_result("login"),
        command_result("runIntegration"),
        command_result("logout"),
    ]

    result = make_service(runner, password_file).run_integration(
        data_file=None,
        inbox_file_name="inbox/Test_Sales_DataLoad_V2.csv",
        integration_name="Test_DataLoad",
        period_range=period_range(),
    )

    assert result.file_name == "inbox/Test_Sales_DataLoad_V2.csv"
    assert [item.args[0] for item in runner.run.call_args_list] == [
        "login",
        "runIntegration",
        "logout",
    ]


def test_run_integration_accepts_administrator_defined_file_extension(
    tmp_path,
) -> None:
    password_file = tmp_path / "password.epw"
    runner = Mock(spec=EPMAutomateRunner)
    runner.run.side_effect = [
        command_result("login"),
        command_result("runIntegration"),
        command_result("logout"),
    ]

    result = make_service(runner, password_file).run_integration(
        data_file=None,
        inbox_file_name="VariableDimensions.dat",
        integration_name="Delimited_Data_Load",
        period_range=DataIntegrationPeriodRange.from_period_names(
            "Jun-19",
            "Jun-19",
        ),
    )

    assert result.file_name == "VariableDimensions.dat"
    assert "inputFileName=VariableDimensions.dat" in (
        runner.run.call_args_list[1].args
    )


def test_run_integration_failure_still_logs_out(tmp_path) -> None:
    password_file = tmp_path / "password.epw"
    runner = Mock(spec=EPMAutomateRunner)
    runner.run.side_effect = [
        command_result("login"),
        command_result(
            "runIntegration",
            return_code=1,
            stderr="Integration failed",
        ),
        command_result("logout"),
    ]

    with pytest.raises(EPMAutomateCommandError, match="Integration failed"):
        make_service(runner, password_file).run_integration(
            data_file=None,
            inbox_file_name="Test.csv",
            integration_name="Test_DataLoad",
            period_range=period_range(),
        )

    assert runner.run.call_args_list[-1] == call("logout", check=False)

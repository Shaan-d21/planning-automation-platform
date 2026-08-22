"""Tests for native Planning data loading through EPM Automate."""

from __future__ import annotations

from unittest.mock import Mock, call

import pytest

from app.automation.epm_automate_runner import EPMAutomateRunner
from app.models.command import CommandResult
from app.services.epm_automate_data_service import EPMAutomateDataService
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


def make_service(runner: Mock, password_file) -> EPMAutomateDataService:
    return EPMAutomateDataService(
        runner,
        username="epm.user",
        password_file=password_file,
        base_url="https://example.oraclecloud.com",
    )


@pytest.mark.parametrize("extension", [".csv", ".txt", ".zip"])
def test_data_import_passes_file_override(tmp_path, extension: str) -> None:
    data_file = tmp_path / f"PlanData{extension}"
    data_file.write_bytes(b"content")
    password_file = tmp_path / "password.epw"
    runner = Mock(spec=EPMAutomateRunner)
    runner.run.side_effect = [
        command_result("login"),
        command_result("uploadFile"),
        command_result("importData"),
        command_result("logout"),
    ]

    result = make_service(runner, password_file).load_data(
        data_file=data_file,
        inbox_file_name=None,
        job_name="Import Plan Data",
        error_file_name="DataErrors",
    )

    assert result.file_name == data_file.name
    assert runner.run.call_args_list[2] == call(
        "importData",
        "Import Plan Data",
        data_file.name,
        "errorFile=DataErrors.zip",
        check=False,
    )


def test_existing_inbox_file_skips_upload(tmp_path) -> None:
    password_file = tmp_path / "password.epw"
    runner = Mock(spec=EPMAutomateRunner)
    runner.run.side_effect = [
        command_result("login"),
        command_result("importData"),
        command_result("logout"),
    ]

    result = make_service(runner, password_file).load_data(
        data_file=None,
        inbox_file_name="PlanData.txt",
        job_name="Import Plan Data",
    )

    assert result.replaced_existing is False
    assert [item.args[0] for item in runner.run.call_args_list] == [
        "login",
        "importData",
        "logout",
    ]


def test_configured_job_file_omits_runtime_filename(tmp_path) -> None:
    password_file = tmp_path / "password.epw"
    runner = Mock(spec=EPMAutomateRunner)
    runner.run.side_effect = [
        command_result("login"),
        command_result("importData"),
        command_result("logout"),
    ]

    result = make_service(runner, password_file).load_data(
        data_file=None,
        inbox_file_name=None,
        job_name="Import Plan Data",
    )

    assert result.file_name is None
    assert runner.run.call_args_list[1] == call(
        "importData",
        "Import Plan Data",
        check=False,
    )


def test_import_failure_still_logs_out(tmp_path) -> None:
    password_file = tmp_path / "password.epw"
    runner = Mock(spec=EPMAutomateRunner)
    runner.run.side_effect = [
        command_result("login"),
        command_result(
            "importData",
            return_code=1,
            stderr="Data import failed",
        ),
        command_result("logout"),
    ]

    with pytest.raises(
        EPMAutomateCommandError,
        match="Data import failed",
    ):
        make_service(runner, password_file).load_data(
            data_file=None,
            inbox_file_name="PlanData.csv",
            job_name="Import Plan Data",
        )

    assert runner.run.call_args_list[-1] == call(
        "logout",
        check=False,
    )

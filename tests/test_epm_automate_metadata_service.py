"""Tests for metadata loading through EPM Automate."""

from __future__ import annotations

from unittest.mock import Mock, call

import pytest

from app.automation.epm_automate_runner import EPMAutomateRunner
from app.models.command import CommandResult
from app.services.epm_automate_metadata_service import (
    EPMAutomateMetadataService,
)
from app.utils.exceptions import (
    EPMAutomateAuthenticationError,
    EPMAutomateCommandError,
)


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


def make_service(runner: Mock, password_file) -> EPMAutomateMetadataService:
    return EPMAutomateMetadataService(
        runner,
        username="epm.user",
        password_file=password_file,
        base_url="https://example.oraclecloud.com",
    )


def test_csv_upload_uses_filename_from_saved_job(tmp_path) -> None:
    metadata_file = tmp_path / "Account.csv"
    metadata_file.write_text("Account,Parent", encoding="utf-8")
    password_file = tmp_path / "password.epw"
    password_file.write_text("encrypted", encoding="utf-8")
    runner = Mock(spec=EPMAutomateRunner)
    runner.run.side_effect = [
        command_result("login"),
        command_result("uploadFile"),
        command_result("importMetadata"),
        command_result("logout"),
    ]

    result = make_service(runner, password_file).load_metadata(
        metadata_file=metadata_file,
        inbox_file_name=None,
        job_name="Import Account",
    )

    assert result.file_name == "Account.csv"
    assert result.replaced_existing is False
    assert runner.run.call_args_list == [
        call(
            "login",
            "epm.user",
            str(password_file),
            "https://example.oraclecloud.com",
            check=False,
        ),
        call("uploadFile", str(metadata_file.resolve()), check=False),
        call("importMetadata", "Import Account", check=False),
        call("logout", check=False),
    ]


def test_zip_import_passes_override_and_error_file(tmp_path) -> None:
    metadata_file = tmp_path / "Metadata.zip"
    metadata_file.write_bytes(b"content")
    password_file = tmp_path / "password.epw"
    password_file.write_text("encrypted", encoding="utf-8")
    runner = Mock(spec=EPMAutomateRunner)
    runner.run.side_effect = [
        command_result("login"),
        command_result("uploadFile"),
        command_result("importMetadata"),
        command_result("logout"),
    ]

    make_service(runner, password_file).load_metadata(
        metadata_file=metadata_file,
        inbox_file_name=None,
        job_name="Import Metadata",
        error_file_name="Errors",
    )

    assert runner.run.call_args_list[2] == call(
        "importMetadata",
        "Import Metadata",
        "Metadata.zip",
        "errorFile=Errors.zip",
        check=False,
    )


def test_existing_file_is_deleted_and_upload_is_retried(tmp_path) -> None:
    metadata_file = tmp_path / "Metadata.zip"
    metadata_file.write_bytes(b"content")
    password_file = tmp_path / "password.epw"
    password_file.write_text("encrypted", encoding="utf-8")
    runner = Mock(spec=EPMAutomateRunner)
    runner.run.side_effect = [
        command_result("login"),
        command_result(
            "uploadFile",
            return_code=1,
            stderr="File already exists",
        ),
        command_result("deleteFile"),
        command_result("uploadFile"),
        command_result("importMetadata"),
        command_result("logout"),
    ]

    result = make_service(runner, password_file).load_metadata(
        metadata_file=metadata_file,
        inbox_file_name=None,
        job_name="Import Metadata",
    )

    assert result.replaced_existing is True
    assert (
        call("deleteFile", "Metadata.zip", check=True)
        in runner.run.call_args_list
    )


def test_unrelated_upload_failure_does_not_delete(tmp_path) -> None:
    metadata_file = tmp_path / "Metadata.zip"
    metadata_file.write_bytes(b"content")
    password_file = tmp_path / "password.epw"
    password_file.write_text("encrypted", encoding="utf-8")
    runner = Mock(spec=EPMAutomateRunner)
    runner.run.side_effect = [
        command_result("login"),
        command_result(
            "uploadFile",
            return_code=1,
            stderr="Insufficient privileges",
        ),
        command_result("logout"),
    ]

    with pytest.raises(
        EPMAutomateCommandError,
        match="Insufficient privileges",
    ):
        make_service(runner, password_file).load_metadata(
            metadata_file=metadata_file,
            inbox_file_name=None,
            job_name="Import Metadata",
        )

    assert not any(
        command_call.args[0] == "deleteFile"
        for command_call in runner.run.call_args_list
    )


def test_login_failure_raises_authentication_error(tmp_path) -> None:
    password_file = tmp_path / "password.epw"
    runner = Mock(spec=EPMAutomateRunner)
    runner.run.return_value = command_result(
        "login",
        return_code=9,
        stderr="Invalid credentials",
    )

    with pytest.raises(
        EPMAutomateAuthenticationError,
        match="Invalid credentials",
    ):
        make_service(runner, password_file).load_metadata(
            metadata_file=None,
            inbox_file_name="Metadata.zip",
            job_name="Import Metadata",
        )

    runner.run.assert_called_once()


def test_login_removes_planning_context_from_base_url(tmp_path) -> None:
    password_file = tmp_path / "password.epw"
    runner = Mock(spec=EPMAutomateRunner)
    runner.run.side_effect = [
        command_result("login"),
        command_result("importMetadata"),
        command_result("logout"),
    ]
    service = EPMAutomateMetadataService(
        runner,
        username="epm.user",
        password_file=password_file,
        base_url=(
            "https://example.oraclecloud.com/HyperionPlanning/"
        ),
    )

    service.load_metadata(
        metadata_file=None,
        inbox_file_name="Metadata.zip",
        job_name="Import Metadata",
    )

    assert runner.run.call_args_list[0] == call(
        "login",
        "epm.user",
        str(password_file),
        "https://example.oraclecloud.com",
        check=False,
    )

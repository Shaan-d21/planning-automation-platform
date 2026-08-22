"""Tests for safe EPM Automate process execution."""

from __future__ import annotations

import subprocess
from unittest.mock import Mock

import pytest

from app.automation.epm_automate_runner import EPMAutomateRunner
from app.utils.exceptions import (
    EPMAutomateCommandError,
    EPMAutomateNotInstalledError,
    EPMAutomateTimeoutError,
)


def test_runner_executes_argument_list_without_shell(tmp_path) -> None:
    executable = tmp_path / "epmautomate.bat"
    executable.write_text("@echo off", encoding="utf-8")
    process_runner = Mock(
        return_value=subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="Command completed successfully",
            stderr="",
        )
    )
    runner = EPMAutomateRunner(
        str(executable),
        timeout=30,
        process_runner=process_runner,
    )

    result = runner.run("uploadFile", r"C:\Metadata\Account.csv")

    assert result.is_successful
    process_runner.assert_called_once_with(
        [
            str(executable),
            "uploadFile",
            r"C:\Metadata\Account.csv",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        shell=False,
    )


def test_runner_raises_for_nonzero_exit_code(tmp_path) -> None:
    executable = tmp_path / "epmautomate.bat"
    executable.write_text("@echo off", encoding="utf-8")
    process_runner = Mock(
        return_value=subprocess.CompletedProcess(
            args=[],
            returncode=7,
            stdout="",
            stderr="Invalid parameter",
        )
    )
    runner = EPMAutomateRunner(
        str(executable),
        timeout=30,
        process_runner=process_runner,
    )

    with pytest.raises(EPMAutomateCommandError, match="Invalid parameter"):
        runner.run("importMetadata", "Import Account")


def test_runner_translates_timeout(tmp_path) -> None:
    executable = tmp_path / "epmautomate.bat"
    executable.write_text("@echo off", encoding="utf-8")
    process_runner = Mock(
        side_effect=subprocess.TimeoutExpired(
            cmd="epmautomate",
            timeout=5,
        )
    )
    runner = EPMAutomateRunner(
        str(executable),
        timeout=5,
        process_runner=process_runner,
    )

    with pytest.raises(EPMAutomateTimeoutError, match="5 seconds"):
        runner.run("importMetadata", "Import Account")


def test_runner_rejects_missing_explicit_executable(tmp_path) -> None:
    with pytest.raises(EPMAutomateNotInstalledError, match="does not exist"):
        EPMAutomateRunner(
            str(tmp_path / "missing" / "epmautomate.bat"),
            timeout=30,
        )

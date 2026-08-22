"""Safe process adapter for the Oracle EPM Automate utility."""

from __future__ import annotations

import logging
import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from app.models.command import CommandResult
from app.utils.exceptions import (
    EPMAutomateCommandError,
    EPMAutomateNotInstalledError,
    EPMAutomateTimeoutError,
)

ProcessRunner = Callable[..., subprocess.CompletedProcess[str]]


class EPMAutomateRunner:
    """Execute EPM Automate commands without invoking a command shell."""

    def __init__(
        self,
        executable: str,
        *,
        timeout: float,
        logger: logging.Logger | None = None,
        process_runner: ProcessRunner = subprocess.run,
    ) -> None:
        """Initialize and resolve the configured EPM Automate executable."""
        self._executable = self._resolve_executable(executable)
        self._timeout = timeout
        self._logger = logger or logging.getLogger(__name__)
        self._process_runner = process_runner

    def run(
        self,
        command: str,
        *arguments: str,
        check: bool = True,
    ) -> CommandResult:
        """Run one EPM Automate command and return its normalized result."""
        command_arguments = [
            self._executable,
            command,
            *map(str, arguments),
        ]
        self._logger.info("EPM Automate command started: %s.", command)

        try:
            completed = self._process_runner(
                command_arguments,
                check=False,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            self._logger.error(
                "EPM Automate command timed out: %s.",
                command,
            )
            raise EPMAutomateTimeoutError(command, self._timeout) from exc
        except FileNotFoundError as exc:
            raise EPMAutomateNotInstalledError(
                f"EPM Automate executable was not found: "
                f"'{self._executable}'."
            ) from exc
        except OSError as exc:
            raise EPMAutomateCommandError(
                command,
                -1,
                f"Unable to start EPM Automate: {exc}",
            ) from exc

        result = CommandResult(
            command=command,
            return_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )
        self._logger.info(
            "EPM Automate command finished: command=%s, exit_code=%s.",
            command,
            result.return_code,
        )

        if check and not result.is_successful:
            self._logger.error(
                "EPM Automate command failed: command=%s, details=%s.",
                command,
                result.details,
            )
            raise EPMAutomateCommandError(
                command,
                result.return_code,
                result.details,
            )
        return result

    @staticmethod
    def _resolve_executable(value: str) -> str:
        """Resolve an executable name or validate an explicitly given path."""
        configured = value.strip()
        path = Path(configured).expanduser()
        if path.parent != Path("."):
            if not path.is_file():
                raise EPMAutomateNotInstalledError(
                    f"EPM Automate executable does not exist: '{path}'."
                )
            return str(path)

        resolved = shutil.which(configured)
        if resolved is None:
            raise EPMAutomateNotInstalledError(
                "EPM Automate is not installed or is not available on PATH. "
                "Set EPM_AUTOMATE_EXECUTABLE to its full executable path."
            )
        return resolved

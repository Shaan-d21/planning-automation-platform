"""Authenticated EPM Automate session and shared file operations."""

from __future__ import annotations

import logging
from pathlib import Path
from types import TracebackType

from app.automation.epm_automate_runner import EPMAutomateRunner
from app.models.command import CommandResult
from app.utils.exceptions import (
    EPMAutomateAuthenticationError,
    EPMAutomateCommandError,
    EPMAutomateError,
)


class EPMAutomateClient:
    """Manage authentication and reusable EPM Automate operations."""

    def __init__(
        self,
        runner: EPMAutomateRunner,
        *,
        username: str,
        password_file: Path,
        base_url: str,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize with an encrypted password file."""
        self._runner = runner
        self._username = username
        self._password_file = password_file
        self._base_url = self._normalize_base_url(base_url)
        self._logger = logger or logging.getLogger(__name__)
        self._authenticated = False

    def authenticate(self) -> None:
        """Start an authenticated EPM Automate session."""
        result = self._runner.run(
            "login",
            self._username,
            str(self._password_file),
            self._base_url,
            check=False,
        )
        if not result.is_successful:
            raise EPMAutomateAuthenticationError(
                "EPM Automate authentication failed: "
                f"{result.details}"
            )
        self._authenticated = True
        self._logger.info("EPM Automate authentication successful.")

    def run(
        self,
        command: str,
        *arguments: str,
        check: bool = True,
    ) -> CommandResult:
        """Run a command within an authenticated session."""
        if not self._authenticated:
            raise EPMAutomateAuthenticationError(
                "EPM Automate session is not authenticated."
            )
        return self._runner.run(
            command,
            *arguments,
            check=check,
        )

    def upload_file(
        self,
        file_path: Path,
        *,
        replace_existing: bool = True,
    ) -> bool:
        """Upload a file and return whether an existing file was replaced."""
        result = self.run(
            "uploadFile",
            str(file_path),
            check=False,
        )
        if result.is_successful:
            self._logger.info(
                "EPM Automate upload successful: '%s'.",
                file_path.name,
            )
            return False

        if (
            not replace_existing
            or not self._indicates_existing_file(result)
        ):
            raise EPMAutomateCommandError(
                "uploadFile",
                result.return_code,
                result.details,
            )

        self._logger.warning(
            "File already exists and will be replaced: '%s'.",
            file_path.name,
        )
        self.run("deleteFile", file_path.name)
        self.run("uploadFile", str(file_path))
        return True

    def close(self) -> None:
        """End the EPM Automate session without masking workflow failures."""
        if not self._authenticated:
            return
        try:
            result = self._runner.run("logout", check=False)
        except EPMAutomateError as exc:
            self._logger.warning(
                "EPM Automate logout could not be executed: %s",
                exc,
            )
        else:
            if not result.is_successful:
                self._logger.warning(
                    "EPM Automate logout failed: %s",
                    result.details,
                )
        finally:
            self._authenticated = False

    def __enter__(self) -> EPMAutomateClient:
        """Authenticate and return this session client."""
        self.authenticate()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Log out when leaving the session context."""
        self.close()

    @staticmethod
    def _normalize_base_url(value: str) -> str:
        normalized = value.rstrip("/")
        planning_suffix = "/HyperionPlanning"
        if normalized.lower().endswith(planning_suffix.lower()):
            return normalized[: -len(planning_suffix)]
        return normalized

    @staticmethod
    def _indicates_existing_file(result: CommandResult) -> bool:
        details = f"{result.stdout}\n{result.stderr}".casefold()
        return any(
            message in details
            for message in (
                "already exists",
                "file or folder exists",
                "file exists",
                "identical to that of a file",
            )
        )

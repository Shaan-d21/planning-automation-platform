"""Native data loading through the Oracle EPM Automate utility."""

from __future__ import annotations

import logging
from pathlib import Path, PurePath

from app.automation.epm_automate_client import EPMAutomateClient
from app.automation.epm_automate_runner import EPMAutomateRunner
from app.models.epm_automate import EPMAutomateDataResult
from app.utils.exceptions import DataImportError, EPMAutomateCommandError


class EPMAutomateDataService:
    """Coordinate a native Planning data import through EPM Automate."""

    _SUPPORTED_EXTENSIONS = frozenset({".csv", ".txt", ".zip"})

    def __init__(
        self,
        runner: EPMAutomateRunner,
        *,
        username: str,
        password_file: Path,
        base_url: str,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the workflow with non-plaintext login parameters."""
        self._logger = logger or logging.getLogger(__name__)
        self._client = EPMAutomateClient(
            runner,
            username=username,
            password_file=password_file,
            base_url=base_url,
            logger=self._logger,
        )

    def load_data(
        self,
        *,
        data_file: Path | None,
        inbox_file_name: str | None,
        job_name: str,
        error_file_name: str | None = None,
    ) -> EPMAutomateDataResult:
        """Log in, optionally upload, import data, and log out."""
        normalized_job_name = job_name.strip()
        if not normalized_job_name:
            raise DataImportError(
                "Data import job name cannot be empty."
            )

        file_path, file_name = self._resolve_source(
            data_file,
            inbox_file_name,
        )
        normalized_error_file = self._normalize_error_file(error_file_name)

        with self._client:
            replaced_existing = (
                self._client.upload_file(file_path)
                if file_path is not None
                else False
            )
            arguments = [normalized_job_name]
            if file_name:
                arguments.append(file_name)
            if normalized_error_file:
                arguments.append(f"errorFile={normalized_error_file}")
            result = self._client.run(
                "importData",
                *arguments,
                check=False,
            )
            if not result.is_successful:
                raise EPMAutomateCommandError(
                    "importData",
                    result.return_code,
                    result.details,
                )

            self._logger.info(
                "EPM Automate data import successful: job='%s', file='%s'.",
                normalized_job_name,
                file_name or "configured in Oracle",
            )
            return EPMAutomateDataResult(
                file_name=file_name,
                job_name=normalized_job_name,
                replaced_existing=replaced_existing,
                command_output=result.stdout.strip(),
            )

    @classmethod
    def _resolve_source(
        cls,
        data_file: Path | None,
        inbox_file_name: str | None,
    ) -> tuple[Path | None, str | None]:
        if data_file is not None and inbox_file_name:
            raise DataImportError(
                "Provide only one local data file or Inbox filename."
            )

        if data_file is not None:
            path = data_file.expanduser().resolve()
            if not path.is_file():
                raise DataImportError(
                    f"Data file does not exist: '{path}'."
                )
            cls._validate_extension(path.name)
            return path, path.name

        if not inbox_file_name:
            return None, None

        file_name = PurePath(str(inbox_file_name)).name.strip()
        cls._validate_extension(file_name)
        return None, file_name

    @classmethod
    def _validate_extension(cls, file_name: str) -> None:
        extension = PurePath(file_name).suffix.lower()
        if extension not in cls._SUPPORTED_EXTENSIONS:
            raise DataImportError(
                "Data file must have a .csv, .txt, or .zip extension."
            )

    @staticmethod
    def _normalize_error_file(value: str | None) -> str | None:
        if not value:
            return None
        file_name = PurePath(value).name.strip()
        if not file_name.lower().endswith(".zip"):
            file_name += ".zip"
        return file_name

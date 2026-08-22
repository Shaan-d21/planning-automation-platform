"""Metadata loading through the Oracle EPM Automate utility."""

from __future__ import annotations

import logging
from pathlib import Path, PurePath

from app.automation.epm_automate_client import EPMAutomateClient
from app.automation.epm_automate_runner import EPMAutomateRunner
from app.models.command import CommandResult
from app.models.epm_automate import EPMAutomateMetadataResult
from app.utils.exceptions import (
    EPMAutomateCommandError,
    MetadataImportError,
)


class EPMAutomateMetadataService:
    """Coordinate a complete EPM Automate metadata import session."""

    _SUPPORTED_EXTENSIONS = frozenset({".csv", ".zip"})

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

    def load_metadata(
        self,
        *,
        metadata_file: Path | None,
        inbox_file_name: str | None,
        job_name: str,
        error_file_name: str | None = None,
    ) -> EPMAutomateMetadataResult:
        """Log in, optionally upload, import metadata, and log out."""
        normalized_job_name = job_name.strip()
        if not normalized_job_name:
            raise MetadataImportError(
                "Metadata import job name cannot be empty."
            )

        file_path, file_name = self._resolve_source(
            metadata_file,
            inbox_file_name,
        )
        normalized_error_file = self._normalize_error_file(error_file_name)
        with self._client:
            replaced_existing = (
                self._client.upload_file(file_path)
                if file_path is not None
                else False
            )
            import_result = self._import_metadata(
                file_name,
                normalized_job_name,
                normalized_error_file,
            )
            return EPMAutomateMetadataResult(
                file_name=file_name,
                job_name=normalized_job_name,
                replaced_existing=replaced_existing,
                command_output=import_result.stdout.strip(),
            )

    def _import_metadata(
        self,
        file_name: str,
        job_name: str,
        error_file_name: str | None,
    ) -> CommandResult:
        arguments = [job_name]
        if PurePath(file_name).suffix.lower() == ".zip":
            arguments.append(file_name)
        if error_file_name:
            arguments.append(f"errorFile={error_file_name}")

        result = self._client.run(
            "importMetadata",
            *arguments,
            check=False,
        )
        if not result.is_successful:
            raise EPMAutomateCommandError(
                "importMetadata",
                result.return_code,
                result.details,
            )
        self._logger.info(
            "EPM Automate metadata import successful: job='%s', file='%s'.",
            job_name,
            file_name,
        )
        return result

    @classmethod
    def _resolve_source(
        cls,
        metadata_file: Path | None,
        inbox_file_name: str | None,
    ) -> tuple[Path | None, str]:
        if (metadata_file is None) == (not inbox_file_name):
            raise MetadataImportError(
                "Provide exactly one local metadata file or Inbox filename."
            )

        if metadata_file is not None:
            path = metadata_file.expanduser().resolve()
            if not path.is_file():
                raise MetadataImportError(
                    f"Metadata file does not exist: '{path}'."
                )
            cls._validate_extension(path.name)
            return path, path.name

        file_name = PurePath(str(inbox_file_name)).name.strip()
        cls._validate_extension(file_name)
        return None, file_name

    @classmethod
    def _validate_extension(cls, file_name: str) -> None:
        extension = PurePath(file_name).suffix.lower()
        if extension not in cls._SUPPORTED_EXTENSIONS:
            raise MetadataImportError(
                "Metadata file must have a .csv or .zip extension."
            )

    @staticmethod
    def _normalize_error_file(value: str | None) -> str | None:
        if not value:
            return None
        file_name = PurePath(value).name.strip()
        if not file_name.lower().endswith(".zip"):
            file_name += ".zip"
        return file_name

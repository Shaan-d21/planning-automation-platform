"""Data Integration execution through the Oracle EPM Automate utility."""

from __future__ import annotations

import logging
from pathlib import Path

from app.automation.epm_automate_client import EPMAutomateClient
from app.automation.epm_automate_runner import EPMAutomateRunner
from app.models.data_integration import (
    DataIntegrationCommandResult,
    DataIntegrationFileReference,
    DataIntegrationPeriodRange,
)
from app.services.data_integration_service import DataIntegrationService
from app.utils.exceptions import (
    DataIntegrationError,
    EPMAutomateCommandError,
)


class EPMAutomateDataIntegrationService:
    """Coordinate a file-based Data Integration run through EPM Automate."""

    def __init__(
        self,
        runner: EPMAutomateRunner,
        *,
        username: str,
        password_file: Path,
        base_url: str,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the workflow with encrypted-file login parameters."""
        self._logger = logger or logging.getLogger(__name__)
        self._client = EPMAutomateClient(
            runner,
            username=username,
            password_file=password_file,
            base_url=base_url,
            logger=self._logger,
        )

    def run_integration(
        self,
        *,
        data_file: Path | None,
        inbox_file_name: str | None,
        integration_name: str,
        period_range: DataIntegrationPeriodRange,
        import_mode: str = "Replace",
        export_mode: str = "Merge",
    ) -> DataIntegrationCommandResult:
        """Log in, upload if needed, run the integration, and log out."""
        normalized_name = integration_name.strip()
        if not normalized_name:
            raise DataIntegrationError(
                "Data Integration name cannot be empty."
            )
        normalized_import = DataIntegrationService.normalize_import_mode(
            import_mode
        )
        normalized_export = DataIntegrationService.normalize_export_mode(
            export_mode
        )
        file_path, file_reference = self._resolve_source(
            data_file,
            inbox_file_name,
        )

        with self._client:
            replaced_existing = (
                self._client.upload_file(file_path)
                if file_path is not None
                else False
            )
            result = self._client.run(
                "runIntegration",
                normalized_name,
                f"importMode={normalized_import}",
                f"exportMode={normalized_export}",
                f"periodName={period_range.oracle_period_name}",
                f"inputFileName={file_reference}",
                check=False,
            )
            if not result.is_successful:
                raise EPMAutomateCommandError(
                    "runIntegration",
                    result.return_code,
                    result.details,
                )

        self._logger.info(
            "EPM Automate Data Integration successful: integration='%s', "
            "file='%s', periods='%s'.",
            normalized_name,
            file_reference,
            period_range.oracle_period_name,
        )
        return DataIntegrationCommandResult(
            integration_name=normalized_name,
            file_name=str(file_reference),
            period_name=period_range.oracle_period_name,
            import_mode=normalized_import,
            export_mode=normalized_export,
            replaced_existing=replaced_existing,
            command_output=result.stdout.strip(),
        )

    @classmethod
    def _resolve_source(
        cls,
        data_file: Path | None,
        inbox_file_name: str | None,
    ) -> tuple[Path | None, DataIntegrationFileReference]:
        if (data_file is None) == (not inbox_file_name):
            raise DataIntegrationError(
                "Provide exactly one local data file or Inbox filename."
            )
        if data_file is not None:
            path = data_file.expanduser().resolve()
            if not path.is_file():
                raise DataIntegrationError(
                    f"Data Integration file does not exist: '{path}'."
                )
            return path, DataIntegrationFileReference.from_default_upload(
                path.name
            )

        return None, DataIntegrationFileReference.from_existing(
            str(inbox_file_name)
        )

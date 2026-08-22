"""Data Map execution through the Oracle EPM Automate utility."""

from __future__ import annotations

import logging
from pathlib import Path

from app.automation.epm_automate_client import EPMAutomateClient
from app.automation.epm_automate_runner import EPMAutomateRunner
from app.models.data_map import DataMapCommandResult
from app.services.data_map_service import DataMapService
from app.utils.exceptions import DataMapError, EPMAutomateCommandError


class EPMAutomateDataMapService:
    """Coordinate one Data Map execution through EPM Automate."""

    def __init__(
        self,
        runner: EPMAutomateRunner,
        *,
        username: str,
        password_file: Path,
        base_url: str,
        logger: logging.Logger | None = None,
    ) -> None:
        self._logger = logger or logging.getLogger(__name__)
        self._client = EPMAutomateClient(
            runner,
            username=username,
            password_file=password_file,
            base_url=base_url,
            logger=self._logger,
        )

    def run_data_map(
        self,
        data_map_name: str,
        *,
        clear_target: bool = False,
    ) -> DataMapCommandResult:
        """Log in, run one Data Map, and always log out."""
        request = DataMapService.build_request(
            data_map_name,
            clear_target=clear_target,
        )
        with self._client:
            result = self._client.run(
                "runPlanTypeMap",
                request.data_map_name,
                f"clearData={str(request.clear_target).lower()}",
                check=False,
            )
            if not result.is_successful:
                raise EPMAutomateCommandError(
                    "runPlanTypeMap",
                    result.return_code,
                    result.details,
                )

        if request.member_overrides or request.exclusion_overrides:
            raise DataMapError(
                "Data Map member overrides require the REST engine."
            )
        return DataMapCommandResult(
            request=request,
            command_output=result.stdout.strip(),
        )

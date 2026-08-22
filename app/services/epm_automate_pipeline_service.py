"""Pipeline execution through the Oracle EPM Automate utility."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path

from app.automation.epm_automate_client import EPMAutomateClient
from app.automation.epm_automate_runner import EPMAutomateRunner
from app.models.pipeline import PipelineCommandResult
from app.services.pipeline_service import PipelineService
from app.utils.exceptions import EPMAutomateCommandError


class EPMAutomatePipelineService:
    """Coordinate one pipeline execution through EPM Automate."""

    def __init__(
        self,
        runner: EPMAutomateRunner,
        *,
        username: str,
        password_file: Path,
        base_url: str,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize with encrypted-file EPM Automate credentials."""
        self._logger = logger or logging.getLogger(__name__)
        self._client = EPMAutomateClient(
            runner,
            username=username,
            password_file=password_file,
            base_url=base_url,
            logger=self._logger,
        )

    def run_pipeline(
        self,
        pipeline_code: str,
        *,
        variables: Mapping[str, str] | None = None,
    ) -> PipelineCommandResult:
        """Log in, run one pipeline, and always log out."""
        normalized_code = PipelineService.validate_pipeline_code(
            pipeline_code
        )
        normalized_variables = PipelineService.normalize_variables(variables)
        variable_arguments = tuple(
            f"{name}={value}" for name, value in normalized_variables
        )

        with self._client:
            result = self._client.run(
                "runPipeline",
                normalized_code,
                *variable_arguments,
                check=False,
            )
            if not result.is_successful:
                raise EPMAutomateCommandError(
                    "runPipeline",
                    result.return_code,
                    result.details,
                )

        self._logger.info(
            "EPM Automate pipeline successful: pipeline_code='%s', "
            "variables=%s.",
            normalized_code,
            [name for name, _ in normalized_variables],
        )
        return PipelineCommandResult(
            pipeline_code=normalized_code,
            variables=normalized_variables,
            command_output=result.stdout.strip(),
        )

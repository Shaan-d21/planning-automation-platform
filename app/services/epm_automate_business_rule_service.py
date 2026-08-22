"""Business Rule execution through the Oracle EPM Automate utility."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path

from app.automation.epm_automate_client import EPMAutomateClient
from app.automation.epm_automate_runner import EPMAutomateRunner
from app.models.business_rule import BusinessRuleCommandResult
from app.services.business_rule_service import BusinessRuleService
from app.utils.exceptions import EPMAutomateCommandError


class EPMAutomateBusinessRuleService:
    """Coordinate one Business Rule execution through EPM Automate."""

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

    def run_rule(
        self,
        rule_name: str,
        *,
        runtime_prompts: Mapping[str, str] | None = None,
    ) -> BusinessRuleCommandResult:
        """Log in, execute one Business Rule, and always log out."""
        normalized_name = BusinessRuleService.validate_rule_name(rule_name)
        normalized_prompts = (
            BusinessRuleService.normalize_runtime_prompts(runtime_prompts)
        )
        prompt_arguments = tuple(
            f"{name}={value}" for name, value in normalized_prompts
        )

        with self._client:
            result = self._client.run(
                "runBusinessRule",
                normalized_name,
                *prompt_arguments,
                check=False,
            )
            if not result.is_successful:
                raise EPMAutomateCommandError(
                    "runBusinessRule",
                    result.return_code,
                    result.details,
                )

        self._logger.info(
            "EPM Automate Business Rule successful: rule='%s', "
            "runtime_prompts=%s.",
            normalized_name,
            [name for name, _ in normalized_prompts],
        )
        return BusinessRuleCommandResult(
            rule_name=normalized_name,
            runtime_prompts=normalized_prompts,
            command_output=result.stdout.strip(),
        )

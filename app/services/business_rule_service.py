"""Oracle Planning Business Rule execution through the REST API."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from app.clients.epm_client import EPMClient
from app.models.business_rule import BusinessRuleSubmission
from app.models.job import JobResult
from app.utils.exceptions import BusinessRuleError, EPMError


class BusinessRuleService:
    """Submit deployed Oracle Planning Business Rules."""

    _JOB_TYPE = "RULES"

    def __init__(
        self,
        client: EPMClient,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the service with the shared EPM client."""
        self._client = client
        self._logger = logger or logging.getLogger(__name__)
        encoded_application = quote(client.application_name, safe="")
        self._jobs_endpoint = (
            f"{client.planning_api_root}/applications/"
            f"{encoded_application}/jobs"
        )

    def start_rule(
        self,
        rule_name: str,
        *,
        runtime_prompts: Mapping[str, str] | None = None,
    ) -> BusinessRuleSubmission:
        """Submit one deployed Business Rule and return its Planning job ID."""
        normalized_name = self.validate_rule_name(rule_name)
        normalized_prompts = self.normalize_runtime_prompts(runtime_prompts)
        payload: dict[str, Any] = {
            "jobType": self._JOB_TYPE,
            "jobName": normalized_name,
        }
        if normalized_prompts:
            payload["parameters"] = dict(normalized_prompts)

        self._logger.info(
            "Business Rule execution started: rule='%s', "
            "runtime_prompts=%s.",
            normalized_name,
            [name for name, _ in normalized_prompts],
        )
        try:
            response = self._client.post(
                self._jobs_endpoint,
                payload=payload,
            )
        except EPMError as exc:
            raise BusinessRuleError(
                f"Unable to start Business Rule '{normalized_name}': {exc}"
            ) from exc

        if not isinstance(response, Mapping):
            raise BusinessRuleError(
                "Oracle Planning returned an unexpected Business Rule "
                "response."
            )

        try:
            job = JobResult.from_response(response)
        except EPMError as exc:
            raise BusinessRuleError(str(exc)) from exc

        if job.is_failed:
            raise BusinessRuleError(
                f"Oracle Planning rejected Business Rule "
                f"'{normalized_name}' with status {job.status}: "
                f"{job.details or job.descriptive_status or 'No details.'}"
            )

        self._logger.info(
            "Business Rule submitted: rule='%s', job_id=%s.",
            normalized_name,
            job.job_id,
        )
        return BusinessRuleSubmission(
            job_id=job.job_id,
            rule_name=normalized_name,
            runtime_prompts=normalized_prompts,
        )

    @staticmethod
    def validate_rule_name(value: str) -> str:
        """Return a non-empty exact Oracle Business Rule name."""
        normalized = str(value).strip()
        if not normalized:
            raise BusinessRuleError(
                "Business Rule name cannot be empty."
            )
        return normalized

    @staticmethod
    def normalize_runtime_prompts(
        values: Mapping[str, str] | None,
    ) -> tuple[tuple[str, str], ...]:
        """Validate runtime prompts while preserving their supplied order."""
        if not values:
            return ()

        normalized: list[tuple[str, str]] = []
        for raw_name, raw_value in values.items():
            name = (
                str(raw_name).strip()
                if raw_name is not None
                else ""
            )
            value = (
                str(raw_value).strip()
                if raw_value is not None
                else ""
            )
            if not name:
                raise BusinessRuleError(
                    "Runtime prompt name cannot be empty."
                )
            if "=" in name:
                raise BusinessRuleError(
                    "Runtime prompt name cannot contain '='."
                )
            if not value:
                raise BusinessRuleError(
                    f"Runtime prompt '{name}' requires a value."
                )
            normalized.append((name, value))
        return tuple(normalized)

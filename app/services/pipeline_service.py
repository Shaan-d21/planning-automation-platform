"""Oracle Data Integration Pipeline execution through the REST API."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

from app.clients.epm_client import EPMClient
from app.models.job import JobDiagnostics, JobResult
from app.models.pipeline import PipelineDetails, PipelineSubmission
from app.utils.exceptions import APIRequestError, PipelineError


class PipelineService:
    """Inspect, submit, and monitor Oracle Data Integration pipelines."""

    _PIPELINE_ENDPOINT = "aif/rest/V1/pipeline"
    _JOBS_ENDPOINT = "aif/rest/V1/jobs"
    _PIPELINE_CODE_PATTERN = re.compile(r"^[A-Za-z0-9]{3,30}$")
    _IMPORT_MODES = {
        "append": "Append",
        "replace": "Replace",
        "map and validate": "Map and Validate",
        "no import": "No Import",
    }
    _EXPORT_MODES = {
        "merge": "Merge",
        "replace": "Replace",
        "accumulate": "Accumulate",
        "no export": "No Export",
    }
    _SEND_MAIL_VALUES = {
        "always": "Always",
        "no": "No",
        "on failure": "On Failure",
        "on success": "On Success",
    }

    def __init__(
        self,
        client: EPMClient,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the service with the shared authenticated client."""
        self._client = client
        self._logger = logger or logging.getLogger(__name__)

    def get_pipeline_details(self, pipeline_code: str) -> PipelineDetails:
        """Retrieve variables, stages, and jobs for a pipeline code."""
        normalized_code = self.validate_pipeline_code(pipeline_code)
        self._logger.info(
            "Retrieving pipeline details: pipeline_code='%s'.",
            normalized_code,
        )
        response = self._client.get(
            self._PIPELINE_ENDPOINT,
            params={"pipelineName": normalized_code},
        )
        if not isinstance(response, Mapping):
            raise APIRequestError(
                "Oracle returned an unexpected pipeline-details response."
            )
        self._raise_for_envelope_error(response, normalized_code)
        details = response.get("response")
        if not isinstance(details, Mapping):
            raise APIRequestError(
                f"Oracle returned no definition for pipeline "
                f"'{normalized_code}'."
            )
        return PipelineDetails.from_response(details)

    def start_pipeline(
        self,
        pipeline_code: str,
        *,
        variables: Mapping[str, str] | None = None,
    ) -> PipelineSubmission:
        """Submit an existing Oracle pipeline for asynchronous execution."""
        normalized_code = self.validate_pipeline_code(pipeline_code)
        normalized_variables = self.normalize_variables(variables)
        payload: dict[str, Any] = {
            "jobName": normalized_code,
            "jobType": "pipeline",
        }
        if normalized_variables:
            payload["variables"] = dict(normalized_variables)

        self._logger.info(
            "Submitting pipeline: pipeline_code='%s', variables=%s.",
            normalized_code,
            [name for name, _ in normalized_variables],
        )
        response = self._client.post(
            self._JOBS_ENDPOINT,
            payload=payload,
        )
        if not isinstance(response, Mapping):
            raise APIRequestError(
                "Oracle returned an unexpected pipeline submission response."
            )
        job = JobResult.from_response(response)
        if job.is_failed:
            raise PipelineError(
                job.details
                or job.descriptive_status
                or f"Oracle rejected pipeline '{normalized_code}'."
            )
        return PipelineSubmission(
            job_id=job.job_id,
            pipeline_code=normalized_code,
            variables=normalized_variables,
        )

    def get_job_status(self, job_id: int) -> JobResult:
        """Retrieve the current pipeline process status."""
        normalized_job_id = self._validate_job_id(job_id)
        response = self._client.get(
            f"{self._JOBS_ENDPOINT}/{normalized_job_id}"
        )
        if not isinstance(response, Mapping):
            raise APIRequestError(
                "Oracle returned an unexpected pipeline job status."
            )
        return JobResult.from_response(
            response,
            fallback_job_id=normalized_job_id,
        )

    def get_failure_diagnostics(self, job: JobResult) -> JobDiagnostics:
        """Return pipeline diagnostics supplied by the status endpoint."""
        return JobDiagnostics(job=job, details=job.raw_response)

    @classmethod
    def validate_pipeline_code(cls, pipeline_code: str) -> str:
        """Validate the immutable Oracle pipeline code."""
        normalized = str(pipeline_code).strip()
        if not cls._PIPELINE_CODE_PATTERN.fullmatch(normalized):
            raise PipelineError(
                "Pipeline code must contain 3 to 30 alphanumeric characters."
            )
        return normalized

    @classmethod
    def normalize_variables(
        cls,
        variables: Mapping[str, str] | None,
    ) -> tuple[tuple[str, str], ...]:
        """Validate variables and normalize documented standard values."""
        if not variables:
            return ()

        normalized: list[tuple[str, str]] = []
        seen: set[str] = set()
        for raw_name, raw_value in variables.items():
            name = str(raw_name).strip()
            value = str(raw_value).strip()
            if not name:
                raise PipelineError("Pipeline variable name cannot be empty.")
            if "=" in name:
                raise PipelineError(
                    f"Pipeline variable name '{name}' cannot contain '='."
                )
            if not value:
                raise PipelineError(
                    f"Pipeline variable '{name}' requires a value."
                )
            normalized_name = name.upper()
            if normalized_name in seen:
                raise PipelineError(
                    f"Pipeline variable '{name}' was supplied more than once."
                )
            seen.add(normalized_name)
            normalized.append(
                (
                    name,
                    cls.normalize_variable(name, value)[1],
                )
            )

        cls._validate_email_variables(dict(normalized))
        return tuple(normalized)

    @classmethod
    def normalize_variable(cls, name: str, value: str) -> tuple[str, str]:
        """Normalize one variable without applying cross-field validation."""
        normalized_name = str(name).strip()
        normalized_value = str(value).strip()
        if not normalized_name:
            raise PipelineError("Pipeline variable name cannot be empty.")
        if "=" in normalized_name:
            raise PipelineError(
                f"Pipeline variable name '{normalized_name}' cannot contain "
                "'='."
            )
        if not normalized_value:
            raise PipelineError(
                f"Pipeline variable '{normalized_name}' requires a value."
            )

        standard_name = normalized_name.upper()
        if standard_name == "IMPORTMODE":
            normalized_value = cls._lookup_value(
                standard_name,
                normalized_value,
                cls._IMPORT_MODES,
            )
        elif standard_name == "EXPORTMODE":
            normalized_value = cls._lookup_value(
                standard_name,
                normalized_value,
                cls._EXPORT_MODES,
            )
        elif standard_name == "SEND_MAIL":
            normalized_value = cls._lookup_value(
                standard_name,
                normalized_value,
                cls._SEND_MAIL_VALUES,
            )
        elif standard_name == "ATTACH_LOGS":
            normalized = normalized_value.casefold()
            if normalized in {"y", "yes"}:
                normalized_value = "Y"
            elif normalized in {"n", "no"}:
                normalized_value = "N"
            else:
                raise PipelineError(
                    "Pipeline variable ATTACH_LOGS must be Y, N, Yes, or No."
                )
        return normalized_name, normalized_value

    @staticmethod
    def _lookup_value(
        name: str,
        value: str,
        supported: Mapping[str, str],
    ) -> str:
        try:
            return supported[value.casefold()]
        except KeyError as exc:
            raise PipelineError(
                f"Unsupported value '{value}' for pipeline variable {name}. "
                f"Supported values: {', '.join(supported.values())}."
            ) from exc

    @staticmethod
    def _validate_email_variables(variables: Mapping[str, str]) -> None:
        normalized = {
            name.upper(): value for name, value in variables.items()
        }
        send_mail = normalized.get("SEND_MAIL", "No")
        if send_mail.casefold() != "no" and not normalized.get("SEND_TO"):
            raise PipelineError(
                "Pipeline variable SEND_TO is required when SEND_MAIL is "
                "enabled."
            )

    @staticmethod
    def _raise_for_envelope_error(
        response: Mapping[str, Any],
        pipeline_code: str,
    ) -> None:
        try:
            status = int(response.get("status", 0))
        except (TypeError, ValueError) as exc:
            raise APIRequestError(
                "Oracle returned an invalid pipeline-details status."
            ) from exc
        if status != 0:
            details = str(
                response.get("details")
                or f"Unable to retrieve pipeline '{pipeline_code}'."
            )
            raise PipelineError(details)

    @staticmethod
    def _validate_job_id(job_id: int) -> int:
        try:
            normalized = int(job_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("job_id must be a positive integer.") from exc
        if normalized <= 0:
            raise ValueError("job_id must be a positive integer.")
        return normalized

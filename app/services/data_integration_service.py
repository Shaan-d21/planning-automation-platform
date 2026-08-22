"""Oracle EPM Data Integration execution through the REST API."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from app.clients.epm_client import EPMClient
from app.models.data_integration import (
    DataIntegrationFileReference,
    DataIntegrationPeriodRange,
    DataIntegrationSubmission,
)
from app.models.job import JobDiagnostics, JobResult
from app.utils.exceptions import APIRequestError, DataIntegrationError


class DataIntegrationService:
    """Submit and monitor Standard Mode Data Integration jobs."""

    _JOBS_ENDPOINT = "aif/rest/V1/jobs"
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
        "subtract": "Subtract",
        "no export": "No Export",
        "check": "Check",
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

    def start_integration(
        self,
        file_name: str | None,
        integration_name: str,
        period_range: DataIntegrationPeriodRange,
        *,
        import_mode: str = "Replace",
        export_mode: str = "Merge",
    ) -> DataIntegrationSubmission:
        """Submit a file-based Data Integration job."""
        normalized_file = (
            self._validate_file_name(file_name) if file_name else None
        )
        normalized_name = self.validate_integration_name(integration_name)
        normalized_import = self.normalize_import_mode(import_mode)
        normalized_export = self.normalize_export_mode(export_mode)
        payload: dict[str, Any] = {
            "jobType": "INTEGRATION",
            "jobName": normalized_name,
            "periodName": period_range.oracle_period_name,
            "importMode": normalized_import,
            "exportMode": normalized_export,
        }
        if normalized_file is not None:
            payload["fileName"] = normalized_file
        self._logger.info(
            "Submitting Data Integration job: integration='%s', file='%s', "
            "periods='%s', import_mode='%s', export_mode='%s'.",
            normalized_name,
            normalized_file or "configured in Oracle",
            period_range.oracle_period_name,
            normalized_import,
            normalized_export,
        )
        response = self._client.post(
            self._JOBS_ENDPOINT,
            payload=payload,
        )
        if not isinstance(response, Mapping):
            raise APIRequestError(
                "Oracle Data Integration returned an unexpected submission "
                "response."
            )
        job = self._submission_job(response, normalized_name)
        return DataIntegrationSubmission(
            job_id=job.job_id,
            integration_name=normalized_name,
            file_name=normalized_file,
            period_name=period_range.oracle_period_name,
            import_mode=normalized_import,
            export_mode=normalized_export,
        )

    def get_job_status(self, job_id: int) -> JobResult:
        """Retrieve a normalized Data Integration job status."""
        normalized_job_id = self._validate_job_id(job_id)
        response = self._client.get(
            f"{self._JOBS_ENDPOINT}/{normalized_job_id}"
        )
        if not isinstance(response, Mapping):
            raise APIRequestError(
                "Oracle Data Integration returned an unexpected job status."
            )
        return JobResult.from_response(
            response,
            fallback_job_id=normalized_job_id,
        )

    def get_failure_diagnostics(
        self,
        job: JobResult,
    ) -> JobDiagnostics:
        """Return failure details already supplied by Data Integration."""
        return JobDiagnostics(job=job, details=job.raw_response)

    @classmethod
    def normalize_import_mode(cls, value: str) -> str:
        """Validate and normalize an Oracle Data Integration import mode."""
        return cls._normalize_mode(
            value,
            cls._IMPORT_MODES,
            mode_type="import",
        )

    @classmethod
    def normalize_export_mode(cls, value: str) -> str:
        """Validate and normalize an Oracle Data Integration export mode."""
        return cls._normalize_mode(
            value,
            cls._EXPORT_MODES,
            mode_type="export",
        )

    @classmethod
    def _normalize_mode(
        cls,
        value: str,
        supported: Mapping[str, str],
        *,
        mode_type: str,
    ) -> str:
        normalized = value.strip().casefold()
        try:
            return supported[normalized]
        except KeyError as exc:
            options = ", ".join(supported.values())
            raise DataIntegrationError(
                f"Unsupported Data Integration {mode_type} mode "
                f"'{value}'. Supported values: {options}."
            ) from exc

    @classmethod
    def _validate_file_name(cls, value: str) -> str:
        return str(DataIntegrationFileReference.from_existing(value))

    @staticmethod
    def validate_integration_name(value: str) -> str:
        """Return one non-empty exact Oracle Data Integration name."""
        normalized = value.strip()
        if not normalized:
            raise DataIntegrationError(
                "Data Integration name cannot be empty."
            )
        return normalized

    def _submission_job(
        self,
        response: Mapping[str, Any],
        integration_name: str,
    ) -> JobResult:
        """Normalize a submission or expose Oracle's application-level error.

        Data Integration can return HTTP 200 even when it rejects a request
        before creating a process.  In that case there is no ``jobId`` and
        the useful explanation is carried in ``details`` or a related field.
        """
        if self._response_job_id(response) is not None:
            return JobResult.from_response(response)

        detail = self._response_detail(response)
        status = response.get("status")
        job_status = response.get("jobStatus")
        self._logger.error(
            "Oracle did not create Data Integration '%s': status=%r, "
            "job_status=%r, detail=%r, response_keys=%s.",
            integration_name,
            status,
            job_status,
            detail,
            sorted(str(key) for key in response),
        )
        if detail:
            raise DataIntegrationError(
                f"Oracle did not start Data Integration "
                f"'{integration_name}': {detail}"
            )

        raise DataIntegrationError(
            f"Oracle did not start Data Integration '{integration_name}' "
            "and returned no process ID. Verify that the exact Integration "
            "name exists, the selected period names match Data Integration "
            "period mappings (for example Jan-27 to Mar-27), and the "
            "configured source file is available."
        )

    @staticmethod
    def _response_job_id(response: Mapping[str, Any]) -> Any | None:
        """Return a process identifier without treating zero as missing."""
        for key in ("jobId", "jobID", "processId", "processID"):
            value = response.get(key)
            if value is not None and str(value).strip():
                return value
        return None

    @staticmethod
    def _response_detail(response: Mapping[str, Any]) -> str | None:
        """Return a bounded Oracle rejection message when one is available."""
        for key in ("details", "detail", "message", "error"):
            value = response.get(key)
            if value is None:
                continue
            if isinstance(value, Mapping):
                nested = (
                    value.get("details")
                    or value.get("detail")
                    or value.get("message")
                    or value.get("error")
                )
                value = nested if nested is not None else value
            normalized = " ".join(str(value).split())
            if normalized:
                return normalized[:1000]
        return None

    @staticmethod
    def _validate_job_id(job_id: int) -> int:
        try:
            normalized = int(job_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("job_id must be a positive integer.") from exc
        if normalized <= 0:
            raise ValueError("job_id must be a positive integer.")
        return normalized

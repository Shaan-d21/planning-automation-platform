"""Native Oracle Planning data import operations."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import PurePath
from typing import Any
from urllib.parse import quote

from app.clients.epm_client import EPMClient
from app.models.data_job import DataJobSubmission
from app.models.job import JobResult
from app.utils.exceptions import DataImportError, EPMError


class DataService:
    """Submit saved native Oracle Planning Import Data jobs."""

    _JOB_TYPE = "IMPORT_DATA"
    _SUPPORTED_EXTENSIONS = frozenset({".csv", ".txt", ".zip"})

    def __init__(
        self,
        client: EPMClient,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the service with the shared EPM client."""
        self._client = client
        self._logger = logger or logging.getLogger(__name__)

    def start_import(
        self,
        file_name: str | None,
        job_name: str,
        *,
        error_file_name: str | None = None,
    ) -> DataJobSubmission:
        """Submit a saved Planning Import Data job and return its job ID."""
        normalized_file_name = (
            PurePath(file_name).name.strip() if file_name else None
        )
        normalized_job_name = job_name.strip()
        self.validate_job_name(normalized_job_name)
        if normalized_file_name is not None:
            self.validate_inputs(normalized_file_name, normalized_job_name)

        parameters: dict[str, str] = {}
        if normalized_file_name is not None:
            parameters["importFileName"] = normalized_file_name
        if error_file_name:
            parameters["errorFile"] = PurePath(error_file_name).name

        payload: dict[str, Any] = {
            "jobType": self._JOB_TYPE,
            "jobName": normalized_job_name,
            "parameters": parameters,
        }
        encoded_application = quote(
            self._client.application_name,
            safe="",
        )
        endpoint = (
            f"{self._client.planning_api_root}/applications/"
            f"{encoded_application}/jobs"
        )

        self._logger.info(
            "Data import started: job='%s', file='%s'.",
            normalized_job_name,
            normalized_file_name,
        )
        try:
            response = self._client.post(endpoint, payload=payload)
        except EPMError as exc:
            raise DataImportError(
                f"Unable to start data import job "
                f"'{normalized_job_name}': {exc}"
            ) from exc

        if not isinstance(response, Mapping):
            raise DataImportError(
                "Oracle Planning returned an unexpected data import response."
            )

        try:
            job = JobResult.from_response(response)
        except EPMError as exc:
            raise DataImportError(str(exc)) from exc

        if job.is_failed:
            raise DataImportError(
                f"Oracle Planning rejected data import job "
                f"'{normalized_job_name}' with status {job.status}: "
                f"{job.details or job.descriptive_status or 'No details.'}"
            )

        self._logger.info("Data Import Job ID: %s.", job.job_id)
        return DataJobSubmission(
            job_id=job.job_id,
            job_name=normalized_job_name,
            file_name=normalized_file_name,
            error_file_name=(
                PurePath(error_file_name).name
                if error_file_name
                else None
            ),
        )

    @classmethod
    def validate_inputs(cls, file_name: str, job_name: str) -> None:
        """Validate a native data import filename and saved job name."""
        if not file_name:
            raise DataImportError("Data filename cannot be empty.")
        if PurePath(file_name).suffix.lower() not in cls._SUPPORTED_EXTENSIONS:
            raise DataImportError(
                "Data filename must have a .csv, .txt, or .zip extension."
            )
        cls.validate_job_name(job_name)

    @staticmethod
    def validate_job_name(job_name: str) -> None:
        if not job_name:
            raise DataImportError("Data import job name cannot be empty.")

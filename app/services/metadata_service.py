"""Oracle Planning metadata import operations."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import PurePath
from typing import Any
from urllib.parse import quote

from app.clients.epm_client import EPMClient
from app.models.job import JobResult
from app.models.metadata_job import (
    MetadataImportMode,
    MetadataJobSubmission,
)
from app.utils.exceptions import EPMError, MetadataImportError


class MetadataService:
    """Submit reusable Oracle Planning Import Metadata jobs."""

    _JOB_TYPE = "IMPORT_METADATA"
    _SUPPORTED_EXTENSIONS = frozenset({".csv", ".zip"})

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
        import_mode: str | MetadataImportMode = (
            MetadataImportMode.JOB_DEFINITION
        ),
        error_file_name: str | None = None,
    ) -> MetadataJobSubmission:
        """Submit a saved Planning Import Metadata job and return its job ID."""
        normalized_file_name = (
            PurePath(file_name).name.strip() if file_name else None
        )
        normalized_job_name = job_name.strip()
        self.validate_job_name(normalized_job_name)
        if normalized_file_name is not None:
            self.validate_inputs(normalized_file_name, normalized_job_name)

        try:
            normalized_mode = MetadataImportMode.parse(import_mode)
        except ValueError as exc:
            raise MetadataImportError(str(exc)) from exc

        parameters: dict[str, str] = {}
        if (
            normalized_file_name is not None
            and PurePath(normalized_file_name).suffix.lower() == ".zip"
        ):
            parameters["importZipFileName"] = normalized_file_name
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
            "Metadata import started: job='%s', file='%s', mode='%s'.",
            normalized_job_name,
            normalized_file_name or "configured in Oracle",
            normalized_mode.value,
        )
        try:
            response = self._client.post(endpoint, payload=payload)
        except EPMError as exc:
            raise MetadataImportError(
                f"Unable to start metadata import job "
                f"'{normalized_job_name}': {exc}"
            ) from exc

        if not isinstance(response, Mapping):
            raise MetadataImportError(
                "Oracle Planning returned an unexpected metadata import "
                "response."
            )

        try:
            job = JobResult.from_response(response)
        except EPMError as exc:
            raise MetadataImportError(str(exc)) from exc

        if job.is_failed:
            raise MetadataImportError(
                f"Oracle Planning rejected metadata import job "
                f"'{normalized_job_name}' with status {job.status}: "
                f"{job.details or job.descriptive_status or 'No details.'}"
            )

        self._logger.info("Import Job ID: %s.", job.job_id)
        return MetadataJobSubmission(
            job_id=job.job_id,
            job_name=normalized_job_name,
            file_name=normalized_file_name,
            import_mode=normalized_mode,
            error_file_name=(
                PurePath(error_file_name).name
                if error_file_name
                else None
            ),
        )

    @classmethod
    def validate_inputs(cls, file_name: str, job_name: str) -> None:
        """Validate a saved job name and supported metadata filename."""
        if not file_name:
            raise MetadataImportError("Metadata filename cannot be empty.")
        if PurePath(file_name).suffix.lower() not in cls._SUPPORTED_EXTENSIONS:
            raise MetadataImportError(
                "Metadata filename must have a .csv or .zip extension."
            )
        cls.validate_job_name(job_name)

    @staticmethod
    def validate_job_name(job_name: str) -> None:
        """Validate a saved Metadata Import job name."""
        if not job_name:
            raise MetadataImportError(
                "Metadata import job name cannot be empty."
            )

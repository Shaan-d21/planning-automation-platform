"""Safe execution of saved Oracle Planning Cube Refresh jobs."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from urllib.parse import quote

from app.clients.epm_client import EPMClient
from app.models.cube_refresh import CubeRefreshSubmission
from app.models.job import JobDiagnostics, JobResult
from app.utils.exceptions import APIRequestError, CubeRefreshError


class CubeRefreshService:
    """Submit and monitor an existing CUBE_REFRESH job through REST."""

    _JOB_TYPE = "CUBE_REFRESH"

    def __init__(
        self,
        client: EPMClient,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._client = client
        self._logger = logger or logging.getLogger(__name__)
        application = quote(client.application_name, safe="")
        self._jobs_endpoint = (
            f"{client.planning_api_root}/applications/{application}/jobs"
        )

    def start_refresh(self, job_name: str) -> CubeRefreshSubmission:
        """Submit an existing saved cube refresh job."""
        normalized = str(job_name).strip()
        if not normalized:
            raise CubeRefreshError("Cube Refresh job name cannot be empty.")
        try:
            response = self._client.post(
                self._jobs_endpoint,
                payload={
                    "jobType": self._JOB_TYPE,
                    "jobName": normalized,
                },
            )
        except APIRequestError as exc:
            if exc.status_code == 400:
                raise CubeRefreshError(
                    f"Oracle could not execute the saved Cube Refresh job "
                    f"'{normalized}'. Confirm that the job still exists in "
                    "this Planning application and that the connected user "
                    f"can run it. Oracle response: {exc}"
                ) from exc
            raise
        if not isinstance(response, Mapping):
            raise APIRequestError(
                "Oracle returned an unexpected Cube Refresh response."
            )
        job = JobResult.from_response(response)
        if job.is_failed:
            raise CubeRefreshError(
                job.details
                or job.descriptive_status
                or f"Oracle rejected Cube Refresh job '{normalized}'."
            )
        return CubeRefreshSubmission(job_id=job.job_id, job_name=normalized)

    def get_job_status(self, job_id: int) -> JobResult:
        """Retrieve current refresh job status."""
        response = self._client.get(f"{self._jobs_endpoint}/{int(job_id)}")
        if not isinstance(response, Mapping):
            raise APIRequestError(
                "Oracle returned an unexpected Cube Refresh job status."
            )
        return JobResult.from_response(response, fallback_job_id=int(job_id))

    def get_failure_diagnostics(self, job: JobResult) -> JobDiagnostics:
        """Return diagnostics available on the status response."""
        return JobDiagnostics(job=job, details=job.raw_response)

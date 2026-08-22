"""Oracle Planning Data Map execution through the REST API."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from app.clients.epm_client import EPMClient
from app.models.data_map import DataMapExecutionRequest, DataMapSubmission
from app.models.job import JobDiagnostics, JobResult
from app.utils.exceptions import APIRequestError, DataMapError


class DataMapService:
    """Submit existing Data Maps and expose reusable job-monitor operations."""

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

    def start_data_map(
        self,
        data_map_name: str,
        *,
        clear_target: bool = False,
        member_overrides: Mapping[str, str] | None = None,
        exclusion_overrides: Mapping[str, str] | None = None,
    ) -> DataMapSubmission:
        """Submit a previously defined Data Map for asynchronous execution."""
        request = self.build_request(
            data_map_name,
            clear_target=clear_target,
            member_overrides=member_overrides,
            exclusion_overrides=exclusion_overrides,
        )
        parameters: dict[str, Any] = {"clearData": request.clear_target}
        if request.member_overrides:
            parameters["overrideMembersMap"] = dict(
                request.member_overrides
            )
        if request.exclusion_overrides:
            parameters["overrideExclusionMembersMap"] = dict(
                request.exclusion_overrides
            )
        payload = {
            "jobType": "PLAN_TYPE_MAP",
            "jobName": request.data_map_name,
            "parameters": parameters,
        }

        self._logger.info(
            "Submitting Data Map: name='%s', clear_target=%s, overrides=%s, "
            "exclusions=%s.",
            request.data_map_name,
            request.clear_target,
            [name for name, _ in request.member_overrides],
            [name for name, _ in request.exclusion_overrides],
        )
        response = self._client.post(
            self._jobs_endpoint,
            payload=payload,
        )
        if not isinstance(response, Mapping):
            raise APIRequestError(
                "Oracle Planning returned an unexpected Data Map response."
            )
        job = JobResult.from_response(response)
        if job.is_failed:
            raise DataMapError(
                job.details
                or job.descriptive_status
                or f"Oracle rejected Data Map '{request.data_map_name}'."
            )
        return DataMapSubmission(job_id=job.job_id, request=request)

    def get_job_status(self, job_id: int) -> JobResult:
        """Retrieve the current status of an asynchronous Data Map job."""
        response = self._client.get(f"{self._jobs_endpoint}/{int(job_id)}")
        if not isinstance(response, Mapping):
            raise APIRequestError(
                "Oracle Planning returned an unexpected Data Map job status."
            )
        return JobResult.from_response(response, fallback_job_id=int(job_id))

    def get_failure_diagnostics(self, job: JobResult) -> JobDiagnostics:
        """Return the diagnostic detail available on the job response."""
        return JobDiagnostics(job=job, details=job.raw_response)

    @classmethod
    def build_request(
        cls,
        data_map_name: str,
        *,
        clear_target: bool = False,
        member_overrides: Mapping[str, str] | None = None,
        exclusion_overrides: Mapping[str, str] | None = None,
    ) -> DataMapExecutionRequest:
        """Validate and normalize Data Map inputs."""
        name = str(data_map_name).strip()
        if not name:
            raise DataMapError("Data Map name cannot be empty.")
        return DataMapExecutionRequest(
            data_map_name=name,
            clear_target=bool(clear_target),
            member_overrides=cls._normalize_selections(
                member_overrides,
                label="member override",
            ),
            exclusion_overrides=cls._normalize_selections(
                exclusion_overrides,
                label="exclusion override",
            ),
        )

    @staticmethod
    def _normalize_selections(
        selections: Mapping[str, str] | None,
        *,
        label: str,
    ) -> tuple[tuple[str, str], ...]:
        if not selections:
            return ()
        normalized: list[tuple[str, str]] = []
        seen: set[str] = set()
        for raw_dimension, raw_selection in selections.items():
            dimension = str(raw_dimension).strip()
            selection = str(raw_selection).strip()
            if not dimension or not selection:
                raise DataMapError(
                    f"Each Data Map {label} requires a dimension and "
                    "member selection."
                )
            key = dimension.casefold()
            if key in seen:
                raise DataMapError(
                    f"Data Map dimension '{dimension}' was supplied more "
                    "than once."
                )
            seen.add(key)
            normalized.append((dimension, selection))
        return tuple(normalized)

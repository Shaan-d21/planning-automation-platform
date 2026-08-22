"""Reusable Oracle Planning job retrieval and diagnostics."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from app.clients.epm_client import EPMClient
from app.models.job import (
    JobDefinition,
    JobDiagnostics,
    JobRecordStatistics,
    JobResult,
)
from app.utils.exceptions import APIRequestError, EPMError


class JobService:
    """Retrieve normalized status and diagnostic details for Planning jobs."""

    _CHILD_JOB_PATTERN = re.compile(r"/childjobs/([^/]+)/details")

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
        self._job_definitions_endpoint = (
            f"{client.planning_api_root}/applications/"
            f"{encoded_application}/jobdefinitions"
        )

    def get_job_definitions(
        self,
        *,
        job_type: str | None = None,
    ) -> tuple[JobDefinition, ...]:
        """Retrieve saved Planning jobs, optionally filtered by job type."""
        params: dict[str, str] | None = None
        if job_type:
            params = {
                "q": json.dumps(
                    {"jobType": job_type.strip().upper()},
                    separators=(",", ":"),
                )
            }

        response = self._client.get(
            self._job_definitions_endpoint,
            params=params,
        )
        if not isinstance(response, Mapping):
            raise APIRequestError(
                "Oracle Planning returned unexpected job definitions."
            )

        items = response.get("items")
        if not isinstance(items, list):
            raise APIRequestError(
                "Oracle Planning job definitions response did not contain "
                "an items collection."
            )

        definitions = tuple(
            JobDefinition.from_response(item)
            for item in items
            if isinstance(item, Mapping)
        )
        return tuple(
            sorted(
                definitions,
                key=lambda definition: definition.job_name.casefold(),
            )
        )

    def get_job(self, job_id: int) -> JobResult:
        """Retrieve and normalize the current status of a Planning job."""
        normalized_job_id = self._validate_job_id(job_id)
        response = self._client.get(
            f"{self._jobs_endpoint}/{normalized_job_id}"
        )
        if not isinstance(response, Mapping):
            raise APIRequestError(
                "Oracle Planning returned an unexpected job status response."
            )
        return JobResult.from_response(
            response,
            fallback_job_id=normalized_job_id,
        )

    def get_job_status(self, job_id: int) -> JobResult:
        """Return the current job status using a descriptive public method."""
        return self.get_job(job_id)

    def get_job_details(
        self,
        job_id: int,
        *,
        message_type: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> Mapping[str, Any]:
        """Retrieve job execution details, including record statistics."""
        normalized_job_id = self._validate_job_id(job_id)
        params = self._detail_params(message_type, offset, limit)
        response = self._client.get(
            f"{self._jobs_endpoint}/{normalized_job_id}/details",
            params=params,
        )
        if not isinstance(response, Mapping):
            raise APIRequestError(
                "Oracle Planning returned unexpected job details."
            )
        return response

    def get_record_statistics(
        self,
        job_id: int,
    ) -> JobRecordStatistics | None:
        """Return official record counts when the job type exposes them."""
        details = self.get_job_details(job_id, limit=1000)
        return JobRecordStatistics.from_response(details)

    def get_failure_diagnostics(
        self,
        job: JobResult,
    ) -> JobDiagnostics:
        """Collect top-level and child-job error details when available."""
        details = self.get_job_details(
            job.job_id,
            message_type="ERROR",
        )
        messages: list[Mapping[str, Any]] = []

        items = details.get("items", [])
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                child_job_id = self._child_job_id(item)
                if child_job_id is None:
                    continue
                try:
                    child_details = self.get_child_job_details(
                        job.job_id,
                        child_job_id,
                        message_type="ERROR",
                    )
                except EPMError as exc:
                    self._logger.warning(
                        "Child-job diagnostics unavailable: job_id=%s, "
                        "child_job_id=%s, error=%s",
                        job.job_id,
                        child_job_id,
                        exc,
                    )
                    continue
                child_items = child_details.get("items", [])
                if isinstance(child_items, list):
                    messages.extend(
                        message
                        for message in child_items
                        if isinstance(message, Mapping)
                    )

        return JobDiagnostics(
            job=job,
            details=details,
            messages=tuple(messages),
        )

    def get_child_job_details(
        self,
        job_id: int,
        child_job_id: str,
        *,
        message_type: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> Mapping[str, Any]:
        """Retrieve message details for a metadata child job."""
        normalized_job_id = self._validate_job_id(job_id)
        encoded_child_job_id = quote(str(child_job_id), safe="")
        params = self._detail_params(message_type, offset, limit)
        response = self._client.get(
            f"{self._jobs_endpoint}/{normalized_job_id}/childjobs/"
            f"{encoded_child_job_id}/details",
            params=params,
        )
        if not isinstance(response, Mapping):
            raise APIRequestError(
                "Oracle Planning returned unexpected child-job details."
            )
        return response

    @staticmethod
    def _validate_job_id(job_id: int) -> int:
        try:
            normalized = int(job_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("job_id must be a positive integer.") from exc
        if normalized <= 0:
            raise ValueError("job_id must be a positive integer.")
        return normalized

    @staticmethod
    def _detail_params(
        message_type: str | None,
        offset: int,
        limit: int,
    ) -> dict[str, Any]:
        if offset < 0:
            raise ValueError("offset cannot be negative.")
        if limit <= 0:
            raise ValueError("limit must be greater than zero.")

        params: dict[str, Any] = {"offset": offset, "limit": limit}
        if message_type:
            params["q"] = json.dumps(
                {"messageType": message_type.upper()},
                separators=(",", ":"),
            )
        return params

    @classmethod
    def _child_job_id(
        cls,
        detail_item: Mapping[str, Any],
    ) -> str | None:
        links = detail_item.get("links", [])
        if not isinstance(links, list):
            return None
        for link in links:
            if not isinstance(link, Mapping):
                continue
            if str(link.get("rel", "")).lower() != "child-job-details":
                continue
            match = cls._CHILD_JOB_PATTERN.search(
                str(link.get("href", ""))
            )
            if match:
                return match.group(1)
        return None

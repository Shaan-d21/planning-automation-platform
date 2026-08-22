"""Strongly typed models for Oracle Planning jobs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from app.utils.exceptions import APIRequestError


class JobStatusCode(IntEnum):
    """Oracle Planning job status codes."""

    IN_PROGRESS = -1
    SUCCESS = 0
    ERROR = 1
    CANCEL_PENDING = 2
    CANCELLED = 3
    INVALID_PARAMETER = 4
    UNKNOWN = 2_147_483_647


@dataclass(frozen=True, slots=True)
class JobDefinition:
    """Saved Oracle Planning job that can be selected for execution."""

    job_name: str
    job_type: str

    @classmethod
    def from_response(
        cls,
        response: Mapping[str, Any],
    ) -> JobDefinition:
        """Create a job definition from an Oracle response item."""
        job_name = str(response.get("jobName", "")).strip()
        job_type = str(response.get("jobType", "")).strip()
        if not job_name or not job_type:
            raise APIRequestError(
                "Oracle Planning returned an invalid job definition."
            )
        return cls(job_name=job_name, job_type=job_type)


@dataclass(frozen=True, slots=True)
class JobResult:
    """Normalized status returned by the Oracle Planning Jobs API."""

    job_id: int
    status: int
    job_name: str | None = None
    job_type: str | None = None
    descriptive_status: str | None = None
    detailed_status: int | None = None
    details: str | None = None
    raw_response: Mapping[str, Any] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )

    @classmethod
    def from_response(
        cls,
        response: Mapping[str, Any],
        *,
        fallback_job_id: int | None = None,
    ) -> JobResult:
        """Create a job result from an Oracle response payload."""
        job_id_value = (
            response.get("jobId")
            or response.get("jobID")
            or fallback_job_id
        )
        status_value = response.get("status")
        if job_id_value is None or status_value is None:
            raise APIRequestError(
                "Oracle Planning returned a job response without jobId "
                "or status."
            )

        try:
            job_id = int(job_id_value)
            status = int(status_value)
        except (TypeError, ValueError) as exc:
            raise APIRequestError(
                "Oracle Planning returned a non-numeric jobId or status."
            ) from exc

        detailed_status_value = response.get("detailedStatus")
        try:
            detailed_status = (
                int(detailed_status_value)
                if detailed_status_value is not None
                else None
            )
        except (TypeError, ValueError) as exc:
            raise APIRequestError(
                "Oracle Planning returned a non-numeric detailedStatus."
            ) from exc

        return cls(
            job_id=job_id,
            status=status,
            job_name=_optional_text(response.get("jobName")),
            job_type=_optional_text(response.get("jobType")),
            descriptive_status=_optional_text(
                response.get("descriptiveStatus")
                or response.get("jobStatus")
            ),
            detailed_status=detailed_status,
            details=_optional_text(response.get("details")),
            raw_response=response,
        )

    @property
    def is_pending(self) -> bool:
        """Return whether the job is still processing or cancellation is pending."""
        return self.status in {
            JobStatusCode.IN_PROGRESS,
            JobStatusCode.CANCEL_PENDING,
        }

    @property
    def is_successful(self) -> bool:
        """Return whether the job completed successfully."""
        return self.status == JobStatusCode.SUCCESS

    @property
    def is_failed(self) -> bool:
        """Return whether the job reached a non-success terminal state."""
        return not self.is_pending and not self.is_successful


@dataclass(frozen=True, slots=True)
class JobDiagnostics:
    """Failure diagnostics collected from job and child-job detail APIs."""

    job: JobResult
    details: Mapping[str, Any] | None = None
    messages: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class JobRecordStatisticsItem:
    """Oracle-reported record counts for one load or metadata dimension."""

    records_read: int
    records_processed: int
    records_rejected: int
    dimension_name: str | None = None
    load_type: str | None = None

    @classmethod
    def from_response(
        cls,
        response: Mapping[str, Any],
    ) -> JobRecordStatisticsItem | None:
        """Parse one Job Details item when it contains record counters."""
        counter_keys = {
            "recordsRead",
            "recordsProcessed",
            "recordsRejected",
        }
        if not counter_keys.intersection(response):
            return None
        return cls(
            records_read=_record_count(response.get("recordsRead")),
            records_processed=_record_count(
                response.get("recordsProcessed")
            ),
            records_rejected=_record_count(
                response.get("recordsRejected")
            ),
            dimension_name=_optional_text(response.get("dimensionName")),
            load_type=_optional_text(response.get("loadType")),
        )

    def to_payload(self) -> dict[str, Any]:
        """Return a JSON-safe representation for retained evidence."""
        return {
            "dimension_name": self.dimension_name,
            "load_type": self.load_type,
            "records_read": self.records_read,
            "records_processed": self.records_processed,
            "records_rejected": self.records_rejected,
        }


@dataclass(frozen=True, slots=True)
class JobRecordStatistics:
    """Aggregated record statistics reported by Oracle Job Details."""

    records_read: int
    records_processed: int
    records_rejected: int
    details: tuple[JobRecordStatisticsItem, ...] = ()

    @classmethod
    def from_response(
        cls,
        response: Mapping[str, Any],
    ) -> JobRecordStatistics | None:
        """Aggregate Oracle detail items without estimating missing counts."""
        raw_items = response.get("items")
        candidates = raw_items if isinstance(raw_items, list) else []
        details = tuple(
            item
            for candidate in candidates
            if isinstance(candidate, Mapping)
            if (item := JobRecordStatisticsItem.from_response(candidate))
            is not None
        )
        if not details:
            root_item = JobRecordStatisticsItem.from_response(response)
            details = (root_item,) if root_item is not None else ()
        if not details:
            return None
        return cls(
            records_read=sum(item.records_read for item in details),
            records_processed=sum(
                item.records_processed for item in details
            ),
            records_rejected=sum(
                item.records_rejected for item in details
            ),
            details=details,
        )

    def to_payload(self) -> dict[str, Any]:
        """Return counters and their Oracle-provided dimension breakdown."""
        return {
            "source": "ORACLE_JOB_DETAILS",
            "records_read": self.records_read,
            "records_processed": self.records_processed,
            "records_rejected": self.records_rejected,
            "details": [item.to_payload() for item in self.details],
        }


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _record_count(value: Any) -> int:
    """Normalize an Oracle numeric counter while rejecting invalid values."""
    if value is None or value == "":
        return 0
    try:
        normalized = int(str(value).replace(",", "").strip())
    except (TypeError, ValueError) as exc:
        raise APIRequestError(
            "Oracle Planning returned a non-numeric record count."
        ) from exc
    if normalized < 0:
        raise APIRequestError(
            "Oracle Planning returned a negative record count."
        )
    return normalized

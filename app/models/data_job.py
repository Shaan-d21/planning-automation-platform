"""Models for native Oracle Planning data import jobs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DataJobSubmission:
    """Result of submitting a native Planning Import Data job."""

    job_id: int
    job_name: str
    file_name: str | None
    error_file_name: str | None = None

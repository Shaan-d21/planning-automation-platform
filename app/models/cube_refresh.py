"""Typed result for a Planning cube refresh job."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CubeRefreshSubmission:
    """Asynchronous cube refresh submission."""

    job_id: int
    job_name: str

"""Typed models for Oracle Planning Data Map execution."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DataMapExecutionRequest:
    """Validated options used to execute an existing Planning Data Map."""

    data_map_name: str
    clear_target: bool = False
    member_overrides: tuple[tuple[str, str], ...] = ()
    exclusion_overrides: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class DataMapSubmission:
    """Asynchronous Data Map submission returned by the Planning REST API."""

    job_id: int
    request: DataMapExecutionRequest


@dataclass(frozen=True, slots=True)
class DataMapCommandResult:
    """Successful Data Map result returned by EPM Automate."""

    request: DataMapExecutionRequest
    command_output: str

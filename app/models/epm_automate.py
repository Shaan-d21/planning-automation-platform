"""Models for EPM Automate workflows."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EPMAutomateMetadataResult:
    """Successful EPM Automate metadata-load result."""

    file_name: str | None
    job_name: str
    replaced_existing: bool
    command_output: str


@dataclass(frozen=True, slots=True)
class EPMAutomateDataResult:
    """Successful EPM Automate native data-load result."""

    file_name: str
    job_name: str
    replaced_existing: bool
    command_output: str

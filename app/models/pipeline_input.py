"""Models for generic external file inputs required by pipelines."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class PipelineFileSource(StrEnum):
    """Supported sources for an external pipeline input file."""

    LOCAL_UPLOAD = "local_upload"
    EXISTING_INBOX = "existing_inbox"


@dataclass(frozen=True, slots=True)
class PipelineFileConsumer:
    """Pipeline stage/job parameter that consumes an input file."""

    stage_name: str
    job_name: str
    job_type: str
    parameter_name: str

    @property
    def display_label(self) -> str:
        """Return a concise stage and job description."""
        return f"{self.stage_name} / {self.job_name}"


@dataclass(frozen=True, slots=True)
class PipelineFileRequirement:
    """One external file needed before a pipeline can start."""

    key: str
    display_name: str
    variable_name: str | None
    configured_reference: str | None
    allowed_extensions: frozenset[str]
    consumers: tuple[PipelineFileConsumer, ...]
    required: bool = True

    @property
    def allows_any_extension(self) -> bool:
        """Return whether Oracle does not constrain the input extension."""
        return not self.allowed_extensions

    @property
    def is_data_integration_input(self) -> bool:
        """Return whether a Data Integration job consumes this file."""
        return any(
            _normalize_job_type(consumer.job_type) == "integration"
            for consumer in self.consumers
        )

    @property
    def has_non_data_integration_consumer(self) -> bool:
        """Return whether another Oracle job type also consumes this file."""
        return any(
            _normalize_job_type(consumer.job_type) != "integration"
            for consumer in self.consumers
        )


@dataclass(frozen=True, slots=True)
class PipelineFileSelection:
    """User-resolved source for one pipeline file requirement."""

    requirement: PipelineFileRequirement
    source: PipelineFileSource
    oracle_reference: str
    local_path: Path | None = None

    @property
    def requires_upload(self) -> bool:
        """Return whether a local file must be uploaded before execution."""
        return self.source is PipelineFileSource.LOCAL_UPLOAD


def _normalize_job_type(value: str) -> str:
    """Normalize Oracle job-type spellings for repository decisions."""
    return "".join(
        character
        for character in value.casefold()
        if character.isalnum()
    )

"""Models for Oracle Planning metadata import jobs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class MetadataImportMode(StrEnum):
    """Supported metadata import behavior.

    Oracle's Planning REST API executes a saved Import Metadata job. Member
    merge/clear behavior is configured in that job definition and cannot be
    overridden through the REST payload.
    """

    JOB_DEFINITION = "job_definition"

    @classmethod
    def parse(cls, value: str | MetadataImportMode) -> MetadataImportMode:
        """Normalize and validate an import mode value."""
        if isinstance(value, cls):
            return value
        normalized = value.strip().lower()
        try:
            return cls(normalized)
        except ValueError as exc:
            raise ValueError(
                "Unsupported metadata import mode "
                f"'{value}'. Oracle Planning REST imports currently support "
                "'job_definition'; configure merge/clear behavior in the "
                "saved Planning Import Metadata job."
            ) from exc


@dataclass(frozen=True, slots=True)
class MetadataJobSubmission:
    """Result of submitting an Oracle Planning metadata import job."""

    job_id: int
    job_name: str
    file_name: str | None
    import_mode: MetadataImportMode
    error_file_name: str | None = None

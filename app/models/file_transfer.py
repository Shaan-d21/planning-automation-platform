"""Models for reusable Oracle EPM file-transfer operations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class FileUploadResult:
    """Result returned after uploading a file to the EPM Inbox."""

    file_name: str
    status: int
    details: str | None = None
    replaced_existing: bool = False
    raw_response: Mapping[str, Any] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )

    @property
    def is_successful(self) -> bool:
        """Return whether Oracle reported a successful upload."""
        return self.status == 0


@dataclass(frozen=True, slots=True)
class OracleRepositoryFile:
    """One selectable external file in the Oracle EPM repository."""

    name: str
    folder: str
    file_type: str
    size_bytes: int | None
    last_modified_epoch_ms: int | None

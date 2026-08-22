"""Environment-scoped Oracle EPM artifact registration models."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class OracleArtifactType(StrEnum):
    """Oracle artifacts managed by the local execution catalog."""

    PIPELINE = "PIPELINE"
    DATA_INTEGRATION = "DATA_INTEGRATION"
    BUSINESS_RULE = "BUSINESS_RULE"
    DATA_MAP = "DATA_MAP"
    METADATA_IMPORT_JOB = "METADATA_IMPORT_JOB"
    DATA_IMPORT_JOB = "DATA_IMPORT_JOB"
    CUBE_REFRESH_JOB = "CUBE_REFRESH_JOB"
    CUBE = "CUBE"


class OracleArtifactStatus(StrEnum):
    """Verification lifecycle for a registered Oracle artifact."""

    VERIFIED = "VERIFIED"
    PENDING = "PENDING"
    MISSING = "MISSING"
    UNAVAILABLE = "UNAVAILABLE"
    INACTIVE = "INACTIVE"


class OracleArtifactSource(StrEnum):
    """How an artifact entered the platform catalog."""

    SEED = "SEED"
    MANUAL = "MANUAL"
    PIPELINE_DISCOVERY = "PIPELINE_DISCOVERY"
    LIVE_DISCOVERY = "LIVE_DISCOVERY"
    LEGACY = "LEGACY"


@dataclass(frozen=True, slots=True)
class OracleEnvironment:
    """Stable non-secret identity for one Oracle application environment."""

    key: str
    base_url: str
    application_name: str

    @classmethod
    def from_settings(
        cls,
        base_url: str,
        application_name: str,
    ) -> OracleEnvironment:
        normalized_url = str(base_url).strip().rstrip("/")
        normalized_application = str(application_name).strip()
        identity = (
            f"{normalized_url.casefold()}|"
            f"{normalized_application.casefold()}"
        )
        return cls(
            key=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
            base_url=normalized_url,
            application_name=normalized_application,
        )

    @property
    def label(self) -> str:
        return f"{self.application_name} at {self.base_url}"


@dataclass(frozen=True, slots=True)
class OracleArtifact:
    """One durable registration and its most recent Oracle verification."""

    artifact_id: int
    environment_key: str
    artifact_type: OracleArtifactType
    oracle_identifier: str
    display_name: str
    description: str | None
    source: OracleArtifactSource
    status: OracleArtifactStatus
    is_active: bool
    consecutive_missing_count: int
    last_verified_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime

    @property
    def is_runnable(self) -> bool:
        """Return whether the artifact may enter a governed run."""
        return self.is_active and self.status in {
            OracleArtifactStatus.VERIFIED,
            OracleArtifactStatus.PENDING,
            OracleArtifactStatus.UNAVAILABLE,
        }

    @property
    def is_verified(self) -> bool:
        return self.is_active and self.status == OracleArtifactStatus.VERIFIED

"""Scoped external-client credentials for Excel and future integrations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.models.access_control import UserAccount


class ApiTokenScope(StrEnum):
    """Least-privilege capabilities available to non-browser clients."""

    PIPELINE_READ = "pipeline.read"
    PIPELINE_RUN = "pipeline.run"
    EXECUTION_READ = "execution.read"


@dataclass(frozen=True, slots=True)
class ApiTokenRecord:
    """Safe persisted token metadata; the secret is never retained."""

    token_id: int
    user_id: int
    name: str
    token_prefix: str
    scopes: frozenset[ApiTokenScope]
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    last_used_ip: str | None
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True)
class IssuedApiToken:
    """One-time token issuance result returned only at creation."""

    record: ApiTokenRecord
    token: str


@dataclass(frozen=True, slots=True)
class AuthenticatedApiToken:
    """Resolved active token and its current platform identity."""

    record: ApiTokenRecord
    user: UserAccount

"""Read-only models for Financial Consolidation and Close."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.models.environment import ApplicationInfo, PlanTypeInfo
from app.models.job import JobDefinition
from app.utils.exceptions import APIRequestError


@dataclass(frozen=True, slots=True)
class FCCSConnectionSnapshot:
    """Verified FCCS connection and safe application metadata."""

    application: ApplicationInfo
    api_version: Mapping[str, Any] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )


@dataclass(frozen=True, slots=True)
class FCCSReadSnapshot:
    """One bounded read-only snapshot used by future FCCS screens."""

    connection: FCCSConnectionSnapshot
    plan_types: tuple[PlanTypeInfo, ...]
    job_definitions: tuple[JobDefinition, ...]


@dataclass(frozen=True, slots=True)
class FCCSJournal:
    """Safe consolidation-journal summary returned by Oracle."""

    label: str
    scenario: str | None = None
    year: str | None = None
    period: str | None = None
    status: str | None = None
    consolidation: str | None = None
    description: str | None = None
    group: str | None = None
    journal_type: str | None = None
    balance_type: str | None = None
    created_by: str | None = None
    modified_by: str | None = None
    posted_by: str | None = None

    @classmethod
    def from_response(cls, response: Mapping[str, Any]) -> FCCSJournal:
        """Normalize one documented FCCS journal response item."""

        label = str(response.get("label") or "").strip()
        if not label:
            raise APIRequestError(
                "Oracle FCCS returned journal metadata without a label."
            )
        return cls(
            label=label,
            scenario=_optional_text(response.get("scenario")),
            year=_optional_text(response.get("year")),
            period=_optional_text(response.get("period")),
            status=_optional_text(response.get("status")),
            consolidation=_optional_text(response.get("consolidation")),
            description=_optional_text(response.get("description")),
            group=_optional_text(response.get("group")),
            journal_type=_optional_text(response.get("journalType")),
            balance_type=_optional_text(response.get("balanceType")),
            created_by=_optional_text(response.get("createdBy")),
            modified_by=_optional_text(response.get("modifiedBy")),
            posted_by=_optional_text(response.get("postedBy")),
        )


@dataclass(frozen=True, slots=True)
class FCCSJournalDetail:
    """Journal summary plus Oracle-provided line items, without mutation."""

    journal: FCCSJournal
    line_items: tuple[Mapping[str, Any], ...] = ()


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None

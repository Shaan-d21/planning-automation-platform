"""Supported read-only Oracle REST foundation for FCCS."""

from __future__ import annotations

import json
from collections.abc import Mapping
from urllib.parse import quote

from app.clients.epm_client import EPMClient
from app.models.environment import ApplicationInfo, PlanTypeInfo
from app.models.fccs import (
    FCCSConnectionSnapshot,
    FCCSJournal,
    FCCSJournalDetail,
    FCCSReadSnapshot,
)
from app.models.job import JobDefinition, JobResult
from app.products.context import classify_business_process
from app.products.contracts import BusinessProcessType
from app.services.application_service import ApplicationService
from app.services.job_service import JobService
from app.utils.exceptions import APIRequestError, ConfigurationError


class FCCSReadService:
    """Expose only documented, non-mutating FCCS resources.

    This service intentionally has no POST, PUT, PATCH, or DELETE method.
    Enabling the FCCS provider remains a separate release gate.
    """

    _MAX_JOURNAL_PAGE_SIZE = 200
    _JOURNAL_FILTERS = frozenset(
        {
            "scenario",
            "year",
            "period",
            "consolidation",
            "status",
            "group",
            "label",
            "description",
            "entity",
        }
    )

    def __init__(self, client: EPMClient) -> None:
        self._client = client
        self._applications = ApplicationService(client)
        self._jobs = JobService(client)
        application = quote(client.application_name, safe="")
        self._journals_endpoint = (
            f"{client.planning_api_root}/applications/{application}/journals"
        )

    def verify_connection(self) -> FCCSConnectionSnapshot:
        """Verify credentials and prove that the configured app is FCCS."""

        version = self._client.authenticate()
        application = self._applications.get_configured_application()
        business_process = classify_business_process(
            product_type=application.product_type,
            application_type=application.application_type,
        )
        if business_process is not BusinessProcessType.FCCS:
            raise ConfigurationError(
                "The configured Oracle application was not verified as "
                "Financial Consolidation and Close."
            )
        return FCCSConnectionSnapshot(
            application=application,
            api_version=dict(version),
        )

    def discover_applications(self) -> tuple[ApplicationInfo, ...]:
        """Return only applications Oracle explicitly identifies as FCCS."""

        return tuple(
            application
            for application in self._applications.get_applications()
            if classify_business_process(
                product_type=application.product_type,
                application_type=application.application_type,
            )
            is BusinessProcessType.FCCS
        )

    def get_plan_types_with_dimensions(self) -> tuple[PlanTypeInfo, ...]:
        """Read FCCS cubes and their dimensions using supported plan APIs."""

        return self._applications.get_plan_types(include_dimensions=True)

    def get_job_definitions(
        self,
        *,
        job_type: str | None = None,
    ) -> tuple[JobDefinition, ...]:
        """Read saved FCCS job definitions without executing them."""

        return self._jobs.get_job_definitions(job_type=job_type)

    def get_job(self, job_id: int) -> JobResult:
        """Read one FCCS job status through the shared Oracle Jobs API."""

        return self._jobs.get_job(job_id)

    def get_journals(
        self,
        *,
        filters: Mapping[str, str] | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[FCCSJournal, ...]:
        """Retrieve a bounded page of consolidation journals."""

        normalized_offset, normalized_limit = self._pagination(offset, limit)
        query = self._journal_filters(filters)
        params: dict[str, object] = {
            "offset": normalized_offset,
            "limit": normalized_limit,
        }
        if query:
            params["q"] = json.dumps(query, separators=(",", ":"))
        response = self._client.get(self._journals_endpoint, params=params)
        items = self._items(response, "journal")
        return tuple(
            FCCSJournal.from_response(item)
            for item in items
            if isinstance(item, Mapping)
        )

    def get_journal_detail(
        self,
        label: str,
        *,
        scenario: str,
        year: str,
        period: str,
        consolidation: str | None = None,
        include_line_items: bool = True,
    ) -> FCCSJournalDetail:
        """Retrieve one exact journal and optionally its line items."""

        normalized_label = str(label).strip()
        if not normalized_label:
            raise ValueError("Journal label cannot be empty.")
        query = self._journal_filters(
            {
                "scenario": scenario,
                "year": year,
                "period": period,
                "consolidation": consolidation or "",
            }
        )
        for required in ("scenario", "year", "period"):
            if required not in query:
                raise ValueError(f"Journal {required} cannot be empty.")
        response = self._client.get(
            f"{self._journals_endpoint}/{quote(normalized_label, safe='')}",
            params={
                "q": json.dumps(query, separators=(",", ":")),
                "lineItems": str(bool(include_line_items)).lower(),
            },
        )
        if not isinstance(response, Mapping):
            raise APIRequestError(
                "Oracle FCCS returned unexpected journal details."
            )
        line_items = response.get("journalLineItems")
        return FCCSJournalDetail(
            journal=FCCSJournal.from_response(response),
            line_items=tuple(
                item
                for item in (line_items if isinstance(line_items, list) else [])
                if isinstance(item, Mapping)
            ),
        )

    def snapshot(self) -> FCCSReadSnapshot:
        """Return the initial connection, dimension, and job foundation."""

        return FCCSReadSnapshot(
            connection=self.verify_connection(),
            plan_types=self.get_plan_types_with_dimensions(),
            job_definitions=self.get_job_definitions(),
        )

    @classmethod
    def _journal_filters(
        cls,
        filters: Mapping[str, str] | None,
    ) -> dict[str, str]:
        if filters is None:
            return {}
        unknown = set(filters) - cls._JOURNAL_FILTERS
        if unknown:
            raise ValueError(
                "Unsupported journal filters: " + ", ".join(sorted(unknown))
            )
        return {
            key: normalized
            for key, value in filters.items()
            if (normalized := str(value or "").strip())
        }

    @classmethod
    def _pagination(cls, offset: int, limit: int) -> tuple[int, int]:
        normalized_offset = int(offset)
        normalized_limit = int(limit)
        if normalized_offset < 0:
            raise ValueError("Journal offset cannot be negative.")
        if not 1 <= normalized_limit <= cls._MAX_JOURNAL_PAGE_SIZE:
            raise ValueError(
                "Journal limit must be between 1 and "
                f"{cls._MAX_JOURNAL_PAGE_SIZE}."
            )
        return normalized_offset, normalized_limit

    @staticmethod
    def _items(response: object, label: str) -> list[object]:
        if not isinstance(response, Mapping):
            raise APIRequestError(
                f"Oracle FCCS returned unexpected {label} metadata."
            )
        items = response.get("items")
        if not isinstance(items, list):
            raise APIRequestError(
                f"Oracle FCCS {label} response did not contain an items "
                "collection."
            )
        return items

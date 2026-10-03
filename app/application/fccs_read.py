"""Application boundary for the read-only FCCS workspace."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping

from app.clients.epm_client import EPMClient
from app.config.settings import Settings
from app.models.fccs import (
    FCCSConnectionSnapshot,
    FCCSJournal,
    FCCSJournalDetail,
    FCCSReadSnapshot,
)
from app.models.job import JobDefinition, JobResult
from app.models.environment import PlanTypeInfo
from app.products.fccs.read_service import FCCSReadService


class FCCSReadApplicationService:
    """Open one authenticated Oracle client per bounded read operation."""

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: Callable[[Settings], EPMClient] = EPMClient,
        logger: logging.Logger | None = None,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory
        self._logger = logger or logging.getLogger(__name__)

    def connection(self) -> FCCSConnectionSnapshot:
        return self._read(lambda service: service.verify_connection())

    def snapshot(self) -> FCCSReadSnapshot:
        return self._read(lambda service: service.snapshot())

    def plan_types(self) -> tuple[PlanTypeInfo, ...]:
        return self._read(
            lambda service: service.get_plan_types_with_dimensions()
        )

    def job_definitions(
        self,
        *,
        job_type: str | None = None,
    ) -> tuple[JobDefinition, ...]:
        return self._read(
            lambda service: service.get_job_definitions(job_type=job_type)
        )

    def job(self, job_id: int) -> JobResult:
        return self._read(lambda service: service.get_job(job_id))

    def journals(
        self,
        *,
        filters: Mapping[str, str] | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[FCCSJournal, ...]:
        return self._read(
            lambda service: service.get_journals(
                filters=filters,
                offset=offset,
                limit=limit,
            )
        )

    def journal_detail(
        self,
        label: str,
        *,
        scenario: str,
        year: str,
        period: str,
        consolidation: str | None = None,
        include_line_items: bool = True,
    ) -> FCCSJournalDetail:
        return self._read(
            lambda service: service.get_journal_detail(
                label,
                scenario=scenario,
                year=year,
                period=period,
                consolidation=consolidation,
                include_line_items=include_line_items,
            )
        )

    def _read(self, operation):
        with self._client_factory(self._settings) as client:
            return operation(FCCSReadService(client))

"""Synchronize Oracle Task Manager reports without retaining source files."""

from __future__ import annotations

import csv
import hashlib
import io
import re
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import delete, insert, select, update

from app.clients.epm_client import EPMClient
from app.config.settings import Settings
from app.infrastructure.database.engine import (
    DatabaseTarget,
    database_for,
    upsert_statement,
    utc_datetime,
)
from app.infrastructure.database.schema import (
    task_manager_sources,
    task_manager_tasks,
)
from app.models.task_manager import (
    TaskManagerSnapshot,
    TaskManagerSource,
    TaskManagerSyncResult,
    TaskManagerTask,
)
from app.utils.exceptions import APIRequestError, ConfigurationError


_MAX_REPORT_BYTES = 25 * 1024 * 1024
_MAX_REPORT_ROWS = 50_000
_HEADER_SCAN_LIMIT = 25

_FIELD_ALIASES = {
    "external_id": ("taskid", "taskidentifier", "id"),
    "name": ("taskname", "task", "name"),
    "schedule_name": ("schedulename", "schedule"),
    "period_name": ("periodname", "period"),
    "status": ("taskstatus", "status"),
    "owner": ("owner", "taskowner"),
    "assignee": ("assignee", "preparer", "assignedto", "preparedby"),
    "approver": ("approver", "reviewer", "approvedby"),
    "organization": ("organizationalunit", "organization", "orgunit"),
    "task_type": ("tasktype", "type"),
    "priority": ("priority",),
    "description": ("taskdescription", "description"),
    "parent_task": ("parenttask", "parent"),
    "dependency": ("predecessors", "predecessor", "dependencies", "dependency"),
    "start_at": ("plannedstartdate", "startdate", "starttime"),
    "due_at": ("plannedenddate", "enddate", "duedate", "duetime"),
    "completed_at": ("completiondate", "actualenddate", "completeddate"),
}


class TaskManagerReportParser:
    """Normalize configurable Task Manager CSV reports into stable records."""

    @classmethod
    def parse(cls, content: bytes) -> tuple[TaskManagerTask, ...]:
        if not content:
            raise ConfigurationError("Oracle returned an empty Task Manager report.")
        if len(content) > _MAX_REPORT_BYTES:
            raise ConfigurationError(
                "The Task Manager report is larger than the 25 MB sync limit."
            )
        text = cls._decode(content)
        rows = list(csv.reader(io.StringIO(text, newline="")))
        header_index = cls._header_index(rows)
        headers = [str(value).strip() for value in rows[header_index]]
        normalized_headers = [cls._normalize_header(value) for value in headers]
        aliases = cls._column_map(normalized_headers)
        if "name" not in aliases and "external_id" not in aliases:
            raise ConfigurationError(
                "The Task Manager report needs a Task Name or Task ID column."
            )

        tasks: list[TaskManagerTask] = []
        seen: set[str] = set()
        for values in rows[header_index + 1 :]:
            if len(tasks) >= _MAX_REPORT_ROWS:
                raise ConfigurationError(
                    "The Task Manager report exceeds the 50,000-row sync limit."
                )
            row = {
                headers[index]: str(values[index]).strip()
                for index in range(min(len(headers), len(values)))
                if headers[index] and str(values[index]).strip()
            }
            if not row:
                continue
            extracted = {
                field: cls._cell(values, column)
                for field, column in aliases.items()
            }
            name = extracted.get("name") or extracted.get("external_id") or ""
            if not name or cls._is_summary_row(name, extracted.get("status")):
                continue
            key_parts = (
                extracted.get("schedule_name") or "",
                extracted.get("period_name") or "",
                extracted.get("external_id") or "",
                name,
                extracted.get("start_at") or "",
            )
            source_key = hashlib.sha256(
                "\x1f".join(key_parts).casefold().encode("utf-8")
            ).hexdigest()
            if source_key in seen:
                continue
            seen.add(source_key)
            tasks.append(
                TaskManagerTask(
                    source_key=source_key,
                    name=name[:500],
                    external_id=cls._bounded(extracted.get("external_id"), 200),
                    schedule_name=cls._bounded(extracted.get("schedule_name"), 300),
                    period_name=cls._bounded(extracted.get("period_name"), 200),
                    status=cls._bounded(extracted.get("status"), 120),
                    owner=cls._bounded(extracted.get("owner"), 300),
                    assignee=cls._bounded(extracted.get("assignee"), 300),
                    approver=cls._bounded(extracted.get("approver"), 300),
                    organization=cls._bounded(extracted.get("organization"), 300),
                    task_type=cls._bounded(extracted.get("task_type"), 160),
                    priority=cls._bounded(extracted.get("priority"), 80),
                    description=extracted.get("description") or None,
                    parent_task=cls._bounded(extracted.get("parent_task"), 500),
                    dependency=extracted.get("dependency") or None,
                    start_at=cls._date(extracted.get("start_at")),
                    due_at=cls._date(extracted.get("due_at")),
                    completed_at=cls._date(extracted.get("completed_at")),
                    attributes=row,
                )
            )
        if not tasks:
            raise ConfigurationError(
                "No Task Manager task rows were found in the generated report."
            )
        return tuple(tasks)

    @staticmethod
    def _decode(content: bytes) -> str:
        for encoding in ("utf-8-sig", "utf-16", "cp1252"):
            try:
                return content.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise ConfigurationError("The Task Manager report encoding is unsupported.")

    @classmethod
    def _header_index(cls, rows: list[list[str]]) -> int:
        best_index = 0
        best_score = -1
        known = {item for values in _FIELD_ALIASES.values() for item in values}
        for index, row in enumerate(rows[:_HEADER_SCAN_LIMIT]):
            normalized = {cls._normalize_header(value) for value in row}
            score = len(normalized & known)
            if score > best_score:
                best_index = index
                best_score = score
        if best_score < 1:
            raise ConfigurationError(
                "The Task Manager report header could not be recognized."
            )
        return best_index

    @staticmethod
    def _normalize_header(value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", str(value).casefold())

    @staticmethod
    def _column_map(headers: list[str]) -> dict[str, int]:
        result: dict[str, int] = {}
        for field, aliases in _FIELD_ALIASES.items():
            for alias in aliases:
                if alias in headers:
                    result[field] = headers.index(alias)
                    break
        return result

    @staticmethod
    def _cell(values: list[str], index: int) -> str:
        return str(values[index]).strip() if index < len(values) else ""

    @staticmethod
    def _bounded(value: str | None, length: int) -> str | None:
        normalized = str(value or "").strip()
        return normalized[:length] if normalized else None

    @staticmethod
    def _is_summary_row(name: str, status: str | None) -> bool:
        return name.casefold() in {"total", "grand total"} and not status

    @staticmethod
    def _date(value: str | None) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        normalized = text.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
            return parsed.replace(tzinfo=parsed.tzinfo or UTC)
        except ValueError:
            pass
        for pattern in (
            "%m/%d/%Y %I:%M %p",
            "%m/%d/%Y %H:%M",
            "%m/%d/%Y",
            "%d-%b-%Y %I:%M %p",
            "%d-%b-%Y",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(text, pattern).replace(tzinfo=UTC)
            except ValueError:
                continue
        return None


class OracleTaskManagerReportGateway:
    """Generate and download one Task Manager report entirely in memory."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def generate_csv(
        self,
        *,
        report_group: str,
        report_name: str,
        parameters: Mapping[str, str],
    ) -> bytes:
        filename = f"epm-ai-task-manager-{uuid4().hex}.csv"
        payload = {
            "groupName": report_group,
            "reportName": report_name,
            "generatedReportFileName": filename,
            "parameters": dict(parameters),
            "format": "CSV",
            "module": "FCM",
            "runAsync": False,
        }
        with EPMClient(self._settings) as client:
            response = client.post(client.fcm_report_endpoint, payload=payload)
            if not isinstance(response, Mapping):
                raise APIRequestError(
                    "Oracle returned an unexpected Task Manager report response."
                )
            content_link = self._content_link(response)
            return client.get_binary(self._relative_oracle_endpoint(content_link))

    @staticmethod
    def _content_link(response: Mapping[str, Any]) -> str:
        links = response.get("links")
        if isinstance(links, list):
            for link in links:
                if not isinstance(link, Mapping):
                    continue
                relation = str(link.get("rel") or "").casefold()
                href = str(link.get("href") or "").strip()
                if relation == "report-content" and href:
                    return href
        raise APIRequestError(
            "Oracle did not return the generated Task Manager report content link."
        )

    def _relative_oracle_endpoint(self, href: str) -> str:
        parsed = urlparse(href)
        if not parsed.scheme and not parsed.netloc:
            endpoint = href.lstrip("/")
        else:
            endpoint = parsed.path.lstrip("/")
            if parsed.query:
                endpoint = f"{endpoint}?{parsed.query}"
        path = endpoint.split("?", 1)[0].casefold()
        if not path.startswith("hyperionplanning/rest/fcmapi/"):
            raise APIRequestError(
                "Oracle returned an unsupported Task Manager report link."
            )
        # Oracle may publish the report link with an internal host or another
        # alias for the same environment. Only the validated path is retained;
        # EPMClient always rebuilds the request on the configured base URL, so
        # Basic Authentication credentials never follow the returned host.
        return endpoint


class TaskManagerRepository:
    """Persist report configuration and only the latest normalized snapshot."""

    def __init__(self, database_target: DatabaseTarget) -> None:
        self._database = database_for(database_target)

    def snapshot(self, application_id: int) -> TaskManagerSnapshot:
        with self._database.connect() as connection:
            source_row = connection.execute(
                select(task_manager_sources).where(
                    task_manager_sources.c.application_id == application_id
                )
            ).mappings().one_or_none()
            rows = connection.execute(
                select(task_manager_tasks)
                .where(task_manager_tasks.c.application_id == application_id)
                .order_by(
                    task_manager_tasks.c.schedule_name,
                    task_manager_tasks.c.due_at,
                    task_manager_tasks.c.name,
                )
            ).mappings().all()
        source = self._source(source_row) if source_row is not None else None
        return TaskManagerSnapshot(
            source=source,
            tasks=tuple(self._task(row) for row in rows),
        )

    def save_configuration(
        self,
        application_id: int,
        *,
        report_group: str,
        report_name: str,
        parameters: Mapping[str, str],
        actor_user_id: int,
    ) -> None:
        now = datetime.now(UTC)
        values = {
            "application_id": application_id,
            "report_group": report_group,
            "report_name": report_name,
            "parameters": dict(parameters),
            "updated_by_user_id": actor_user_id,
            "updated_at": now,
        }
        with self._database.begin() as connection:
            connection.execute(
                upsert_statement(
                    connection,
                    task_manager_sources,
                    values,
                    index_elements=("application_id",),
                    update_columns=(
                        "report_group",
                        "report_name",
                        "parameters",
                        "updated_by_user_id",
                        "updated_at",
                    ),
                )
            )

    def replace_snapshot(
        self,
        application_id: int,
        tasks: tuple[TaskManagerTask, ...],
    ) -> datetime:
        now = datetime.now(UTC)
        with self._database.begin() as connection:
            connection.execute(
                delete(task_manager_tasks).where(
                    task_manager_tasks.c.application_id == application_id
                )
            )
            connection.execute(
                insert(task_manager_tasks),
                [
                    {
                        "application_id": application_id,
                        "source_key": task.source_key,
                        "external_id": task.external_id,
                        "name": task.name,
                        "schedule_name": task.schedule_name,
                        "period_name": task.period_name,
                        "status": task.status,
                        "owner": task.owner,
                        "assignee": task.assignee,
                        "approver": task.approver,
                        "organization": task.organization,
                        "task_type": task.task_type,
                        "priority": task.priority,
                        "description": task.description,
                        "parent_task": task.parent_task,
                        "dependency": task.dependency,
                        "start_at": task.start_at,
                        "due_at": task.due_at,
                        "completed_at": task.completed_at,
                        "attributes": task.attributes,
                        "synchronized_at": now,
                    }
                    for task in tasks
                ],
            )
            connection.execute(
                update(task_manager_sources)
                .where(task_manager_sources.c.application_id == application_id)
                .values(
                    last_synced_at=now,
                    last_sync_status="SUCCESS",
                    last_sync_record_count=len(tasks),
                    last_error=None,
                )
            )
        return now

    def record_failure(self, application_id: int, error: str) -> None:
        with self._database.begin() as connection:
            connection.execute(
                update(task_manager_sources)
                .where(task_manager_sources.c.application_id == application_id)
                .values(
                    last_sync_status="FAILED",
                    last_error=str(error)[:2000],
                )
            )

    @staticmethod
    def _source(row: Mapping[str, Any]) -> TaskManagerSource:
        return TaskManagerSource(
            application_id=int(row["application_id"]),
            report_group=str(row["report_group"]),
            report_name=str(row["report_name"]),
            parameters={
                str(key): str(value)
                for key, value in dict(row["parameters"] or {}).items()
            },
            updated_at=utc_datetime(row["updated_at"]),
            last_synced_at=utc_datetime(row["last_synced_at"]),
            last_sync_status=row["last_sync_status"],
            last_sync_record_count=int(row["last_sync_record_count"] or 0),
            last_error=row["last_error"],
        )

    @staticmethod
    def _task(row: Mapping[str, Any]) -> TaskManagerTask:
        return TaskManagerTask(
            source_key=str(row["source_key"]),
            external_id=row["external_id"],
            name=str(row["name"]),
            schedule_name=row["schedule_name"],
            period_name=row["period_name"],
            status=row["status"],
            owner=row["owner"],
            assignee=row["assignee"],
            approver=row["approver"],
            organization=row["organization"],
            task_type=row["task_type"],
            priority=row["priority"],
            description=row["description"],
            parent_task=row["parent_task"],
            dependency=row["dependency"],
            start_at=utc_datetime(row["start_at"]),
            due_at=utc_datetime(row["due_at"]),
            completed_at=utc_datetime(row["completed_at"]),
            attributes=dict(row["attributes"] or {}),
        )


class TaskManagerSyncService:
    """Coordinate an Oracle report sync for one application workspace."""

    def __init__(self, settings: Settings, database_target: DatabaseTarget) -> None:
        self._gateway = OracleTaskManagerReportGateway(settings)
        self._repository = TaskManagerRepository(database_target)

    def snapshot(self, application_id: int) -> TaskManagerSnapshot:
        return self._repository.snapshot(application_id)

    def configure(
        self,
        application_id: int,
        *,
        report_group: str,
        report_name: str,
        parameters: Mapping[str, str],
        actor_user_id: int,
    ) -> TaskManagerSnapshot:
        group, name, cleaned_parameters = self._configuration(
            report_group,
            report_name,
            parameters,
        )
        self._repository.save_configuration(
            application_id,
            report_group=group,
            report_name=name,
            parameters=cleaned_parameters,
            actor_user_id=actor_user_id,
        )
        return self._repository.snapshot(application_id)

    def synchronize(
        self,
        application_id: int,
    ) -> TaskManagerSyncResult:
        source = self._repository.snapshot(application_id).source
        if source is None:
            raise ConfigurationError(
                "Configure the Oracle Task Manager report before synchronizing."
            )
        try:
            content = self._gateway.generate_csv(
                report_group=source.report_group,
                report_name=source.report_name,
                parameters=source.parameters,
            )
            tasks = TaskManagerReportParser.parse(content)
            synchronized_at = self._repository.replace_snapshot(
                application_id,
                tasks,
            )
        except Exception as exc:
            self._repository.record_failure(application_id, str(exc))
            raise
        statuses = Counter(task.status or "Unspecified" for task in tasks)
        schedules = {task.schedule_name for task in tasks if task.schedule_name}
        return TaskManagerSyncResult(
            synchronized_at=synchronized_at,
            record_count=len(tasks),
            schedule_count=len(schedules),
            status_counts=dict(statuses),
        )

    @staticmethod
    def _configuration(
        report_group: str,
        report_name: str,
        parameters: Mapping[str, str],
    ) -> tuple[str, str, dict[str, str]]:
        group = str(report_group).strip()
        name = str(report_name).strip()
        if not group or not name:
            raise ConfigurationError("Report group and report name are required.")
        cleaned_parameters = {
            str(key).strip(): str(value).strip()
            for key, value in parameters.items()
            if str(key).strip() and str(value).strip()
        }
        return group, name, cleaned_parameters

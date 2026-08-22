"""PostgreSQL persistence for versioned Oracle Pipeline process designs."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.infrastructure.database.engine import (
    DatabaseTarget,
    database_for,
    utc_datetime,
)
from app.infrastructure.database.schema import (
    planning_process_versions,
    planning_processes,
    process_run_profiles,
)
from app.models.pipeline_process_design import (
    PipelineProcessSummary,
    PipelineProcessVersion,
    PipelineRunProfile,
    ProcessDesignStatus,
)
from app.models.planning_cycle import (
    CycleValueRole,
    CycleVariableBinding,
    PlanningCycleDefinition,
)
from app.models.planning_process import (
    PlanningProcessDefinition,
    PlanningProcessStepDefinition,
    PlanningProcessStepType,
    ProcessContextMode,
)
from app.utils.exceptions import ConfigurationError


class SQLPipelineProcessRepository:
    """Persist immutable drafts and atomically publish active versions."""

    def __init__(self, database_target: DatabaseTarget) -> None:
        self._database = database_for(database_target)

    def save_draft(
        self,
        process: PlanningProcessDefinition,
        cycle: PlanningCycleDefinition,
    ) -> PipelineProcessVersion:
        now = datetime.now(UTC)
        with self._database.begin() as connection:
            self._ensure_process(connection, process.code, now)
            version = int(
                connection.execute(
                    select(
                        func.coalesce(
                            func.max(planning_process_versions.c.version), 0
                        )
                    ).where(
                        func.lower(planning_process_versions.c.process_code)
                        == process.code.casefold()
                    )
                ).scalar_one()
            ) + 1
            connection.execute(
                insert(planning_process_versions).values(
                    process_code=process.code,
                    version=version,
                    status=ProcessDesignStatus.DRAFT.value,
                    process_definition=self._serialize_process(process),
                    cycle_definition=self._serialize_cycle(cycle),
                    created_at=now,
                )
            )
        return PipelineProcessVersion(
            process=process,
            cycle=cycle,
            version=version,
            status=ProcessDesignStatus.DRAFT,
            created_at=now,
        )

    def save_or_replace_draft(
        self,
        process: PlanningProcessDefinition,
        cycle: PlanningCycleDefinition,
    ) -> PipelineProcessVersion:
        latest = self.get_latest(process.code)
        if latest is None or latest.status is not ProcessDesignStatus.DRAFT:
            return self.save_draft(process, cycle)
        now = datetime.now(UTC)
        with self._database.begin() as connection:
            connection.execute(
                update(planning_process_versions)
                .where(
                    func.lower(planning_process_versions.c.process_code)
                    == process.code.casefold(),
                    planning_process_versions.c.version == latest.version,
                    planning_process_versions.c.status
                    == ProcessDesignStatus.DRAFT.value,
                )
                .values(
                    process_definition=self._serialize_process(process),
                    cycle_definition=self._serialize_cycle(cycle),
                    created_at=now,
                )
            )
        updated = self.get(process.code, latest.version)
        if updated is None:
            raise ConfigurationError(
                f"Draft for process '{process.code}' could not be updated."
            )
        return updated

    def activate(self, code: str, version: int) -> PipelineProcessVersion:
        normalized = str(code).strip()
        now = datetime.now(UTC)
        with self._database.begin() as connection:
            exists = connection.execute(
                select(planning_process_versions.c.version).where(
                    func.lower(planning_process_versions.c.process_code)
                    == normalized.casefold(),
                    planning_process_versions.c.version == version,
                )
            ).scalar_one_or_none()
            if exists is None:
                raise ConfigurationError(
                    f"Pipeline process '{code}' version {version} was not found."
                )
            connection.execute(
                update(planning_process_versions)
                .where(
                    func.lower(planning_process_versions.c.process_code)
                    == normalized.casefold(),
                    planning_process_versions.c.status
                    == ProcessDesignStatus.ACTIVE.value,
                )
                .values(status=ProcessDesignStatus.RETIRED.value)
            )
            connection.execute(
                update(planning_process_versions)
                .where(
                    func.lower(planning_process_versions.c.process_code)
                    == normalized.casefold(),
                    planning_process_versions.c.version == version,
                )
                .values(
                    status=ProcessDesignStatus.ACTIVE.value,
                    activated_at=now,
                )
            )
        activated = self.get(code, version)
        if activated is None:
            raise ConfigurationError(
                f"Unable to activate Pipeline process '{code}'."
            )
        return activated

    def get(self, code: str, version: int) -> PipelineProcessVersion | None:
        with self._database.connect() as connection:
            row = connection.execute(
                select(planning_process_versions).where(
                    func.lower(planning_process_versions.c.process_code)
                    == str(code).strip().casefold(),
                    planning_process_versions.c.version == version,
                )
            ).mappings().one_or_none()
        return self._version(row) if row is not None else None

    def list_versions(
        self,
        code: str | None = None,
    ) -> tuple[PipelineProcessVersion, ...]:
        statement = select(planning_process_versions)
        if code:
            statement = statement.where(
                func.lower(planning_process_versions.c.process_code)
                == str(code).strip().casefold()
            )
        statement = statement.order_by(
            planning_process_versions.c.created_at.desc(),
            planning_process_versions.c.version.desc(),
        )
        with self._database.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return tuple(self._version(row) for row in rows)

    def list_active(self) -> tuple[PipelineProcessVersion, ...]:
        with self._database.connect() as connection:
            rows = connection.execute(
                select(planning_process_versions)
                .where(
                    planning_process_versions.c.status
                    == ProcessDesignStatus.ACTIVE.value
                )
                .order_by(func.lower(planning_process_versions.c.process_code))
            ).mappings().all()
        return tuple(self._version(row) for row in rows)

    def list_summaries(self) -> tuple[PipelineProcessSummary, ...]:
        grouped: dict[str, list[PipelineProcessVersion]] = {}
        for item in self.list_versions():
            grouped.setdefault(item.process.code.casefold(), []).append(item)
        summaries = []
        for items in grouped.values():
            latest = max(items, key=lambda item: item.version)
            active = next(
                (
                    item
                    for item in items
                    if item.status is ProcessDesignStatus.ACTIVE
                ),
                None,
            )
            summaries.append(
                PipelineProcessSummary(
                    code=latest.process.code,
                    display_name=latest.process.display_name,
                    pipeline_code=latest.cycle.pipeline_code,
                    latest_version=latest.version,
                    active_version=active.version if active else None,
                    latest_status=latest.status,
                    updated_at=latest.created_at,
                    context_mode=latest.process.context_mode,
                )
            )
        return tuple(
            sorted(summaries, key=lambda item: item.updated_at, reverse=True)
        )

    def get_active(self, code: str) -> PipelineProcessVersion | None:
        with self._database.connect() as connection:
            row = connection.execute(
                select(planning_process_versions).where(
                    func.lower(planning_process_versions.c.process_code)
                    == str(code).strip().casefold(),
                    planning_process_versions.c.status
                    == ProcessDesignStatus.ACTIVE.value,
                )
            ).mappings().one_or_none()
        return self._version(row) if row is not None else None

    def get_latest(self, code: str) -> PipelineProcessVersion | None:
        versions = self.list_versions(code)
        return max(versions, key=lambda item: item.version) if versions else None

    def deactivate(self, code: str) -> PipelineProcessVersion:
        active = self.get_active(code)
        if active is None:
            raise ConfigurationError(
                f"Process '{code}' does not have an active version."
            )
        with self._database.begin() as connection:
            connection.execute(
                update(planning_process_versions)
                .where(
                    func.lower(planning_process_versions.c.process_code)
                    == str(code).strip().casefold(),
                    planning_process_versions.c.version == active.version,
                    planning_process_versions.c.status
                    == ProcessDesignStatus.ACTIVE.value,
                )
                .values(status=ProcessDesignStatus.RETIRED.value)
            )
        return active

    def save_profile(
        self,
        *,
        process_code: str,
        name: str,
        year: str = "",
        start_period: str = "",
        end_period: str = "",
        scenario: str | None = None,
        version: str | None = None,
        pipeline_variables: dict[str, str] | None = None,
        inbox_files: dict[str, str] | None = None,
        required_upload_keys: tuple[str, ...] = (),
    ) -> PipelineRunProfile:
        now = datetime.now(UTC)
        try:
            with self._database.begin() as connection:
                self._ensure_process(connection, process_code, now)
                profile_id = connection.execute(
                    insert(process_run_profiles)
                    .values(
                        process_code=process_code,
                        name=name,
                        year=year,
                        start_period=start_period,
                        end_period=end_period,
                        scenario=scenario,
                        version=version,
                        pipeline_variables=pipeline_variables or {},
                        inbox_files=inbox_files or {},
                        required_upload_keys=list(required_upload_keys),
                        created_at=now,
                    )
                    .returning(process_run_profiles.c.profile_id)
                ).scalar_one()
        except IntegrityError as exc:
            raise ConfigurationError(
                f"Run preset '{name}' already exists for process "
                f"'{process_code}'."
            ) from exc
        profile = self.get_profile(process_code, int(profile_id))
        if profile is None:
            raise ConfigurationError("Run preset could not be saved.")
        return profile

    def get_profile(
        self,
        process_code: str,
        profile_id: int,
    ) -> PipelineRunProfile | None:
        with self._database.connect() as connection:
            row = connection.execute(
                select(process_run_profiles).where(
                    func.lower(process_run_profiles.c.process_code)
                    == str(process_code).strip().casefold(),
                    process_run_profiles.c.profile_id == profile_id,
                    process_run_profiles.c.archived_at.is_(None),
                )
            ).mappings().one_or_none()
        return self._profile(row) if row is not None else None

    def list_profiles(
        self,
        process_code: str,
    ) -> tuple[PipelineRunProfile, ...]:
        with self._database.connect() as connection:
            rows = connection.execute(
                select(process_run_profiles)
                .where(
                    func.lower(process_run_profiles.c.process_code)
                    == str(process_code).strip().casefold(),
                    process_run_profiles.c.archived_at.is_(None),
                )
                .order_by(process_run_profiles.c.created_at.desc())
            ).mappings().all()
        return tuple(self._profile(row) for row in rows)

    def archive_profile(
        self,
        process_code: str,
        profile_id: int,
    ) -> PipelineRunProfile:
        profile = self.get_profile(process_code, profile_id)
        if profile is None:
            raise ConfigurationError(
                f"Run preset {profile_id} was not found for '{process_code}'."
            )
        with self._database.begin() as connection:
            connection.execute(
                update(process_run_profiles)
                .where(
                    func.lower(process_run_profiles.c.process_code)
                    == str(process_code).strip().casefold(),
                    process_run_profiles.c.profile_id == profile_id,
                    process_run_profiles.c.archived_at.is_(None),
                )
                .values(archived_at=datetime.now(UTC))
            )
        return profile

    @staticmethod
    def _ensure_process(connection, process_code: str, now: datetime) -> None:
        row = connection.execute(
            select(planning_processes.c.process_code).where(
                func.lower(planning_processes.c.process_code)
                == process_code.casefold()
            )
        ).scalar_one_or_none()
        if row is None:
            connection.execute(
                insert(planning_processes).values(
                    process_code=process_code,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            connection.execute(
                update(planning_processes)
                .where(planning_processes.c.process_code == row)
                .values(updated_at=now)
            )

    @classmethod
    def _version(cls, row) -> PipelineProcessVersion:
        return PipelineProcessVersion(
            process=cls._deserialize_process(dict(row["process_definition"])),
            cycle=cls._deserialize_cycle(dict(row["cycle_definition"])),
            version=int(row["version"]),
            status=ProcessDesignStatus(str(row["status"])),
            created_at=utc_datetime(row["created_at"]),
            activated_at=utc_datetime(row["activated_at"]),
        )

    @staticmethod
    def _profile(row) -> PipelineRunProfile:
        variables = dict(row["pipeline_variables"] or {})
        inbox_files = dict(row["inbox_files"] or {})
        upload_keys = list(row["required_upload_keys"] or [])
        return PipelineRunProfile(
            profile_id=int(row["profile_id"]),
            process_code=str(row["process_code"]),
            name=str(row["name"]),
            year=str(row["year"]),
            start_period=str(row["start_period"]),
            end_period=str(row["end_period"]),
            scenario=(str(row["scenario"]) if row["scenario"] else None),
            version=(str(row["version"]) if row["version"] else None),
            pipeline_variables=tuple(
                (str(name), str(value)) for name, value in variables.items()
            ),
            inbox_files=tuple(
                (str(name), str(value)) for name, value in inbox_files.items()
            ),
            required_upload_keys=tuple(str(key) for key in upload_keys),
            created_at=utc_datetime(row["created_at"]),
        )

    @staticmethod
    def _serialize_process(
        definition: PlanningProcessDefinition,
    ) -> dict[str, object]:
        return {
            "code": definition.code,
            "displayName": definition.display_name,
            "cycleCode": definition.cycle_code,
            "contextMode": definition.context_mode.value,
            "steps": [
                {
                    "type": step.step_type.value,
                    "name": step.name,
                    "enabled": step.enabled_by_default,
                    "parameters": step.parameters,
                }
                for step in definition.steps
            ],
        }

    @staticmethod
    def _deserialize_process(
        payload: dict[str, object],
    ) -> PlanningProcessDefinition:
        return PlanningProcessDefinition(
            code=str(payload["code"]),
            display_name=str(payload["displayName"]),
            cycle_code=str(payload["cycleCode"]),
            steps=tuple(
                PlanningProcessStepDefinition(
                    step_type=PlanningProcessStepType(str(item["type"])),
                    name=str(item["name"]),
                    enabled_by_default=bool(item.get("enabled", True)),
                    parameters=dict(item.get("parameters") or {}),
                )
                for item in payload["steps"]
            ),
            context_mode=ProcessContextMode(
                str(
                    payload.get(
                        "contextMode",
                        ProcessContextMode.PROMPT_EACH_RUN.value,
                    )
                )
            ),
        )

    @staticmethod
    def _serialize_cycle(
        definition: PlanningCycleDefinition,
    ) -> dict[str, object]:
        return {
            "code": definition.code,
            "displayName": definition.display_name,
            "pipelineCode": definition.pipeline_code,
            "dataMapName": definition.data_map_name,
            "variableBindings": [
                {
                    "role": binding.role.value,
                    "variableName": binding.variable_name,
                    "scope": binding.scope,
                }
                for binding in definition.variable_bindings
            ],
        }

    @staticmethod
    def _deserialize_cycle(
        payload: dict[str, object],
    ) -> PlanningCycleDefinition:
        return PlanningCycleDefinition(
            code=str(payload["code"]),
            display_name=str(payload["displayName"]),
            pipeline_code=str(payload["pipelineCode"]),
            data_map_name=(
                str(payload["dataMapName"])
                if payload.get("dataMapName")
                else None
            ),
            variable_bindings=tuple(
                CycleVariableBinding(
                    role=CycleValueRole(str(item["role"])),
                    variable_name=str(item["variableName"]),
                    scope=str(item["scope"]),
                )
                for item in payload.get("variableBindings", [])
            ),
        )


SQLitePipelineProcessRepository = SQLPipelineProcessRepository

"""Durable Business Rule runtime-prompt registry and validation service."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from sqlalchemy import and_, case, delete, func, insert, select, update

from app.config.settings import Settings
from app.infrastructure.database.engine import database_for, upsert_statement, utc_datetime
from app.infrastructure.database.schema import (
    business_rule_rtp_definitions,
    business_rule_rtp_parameters,
    business_rule_rtp_sync_runs,
)
from app.models.business_rule_rtp import (
    BusinessRuleRTPDefinition,
    RTPRegistryImportResult,
    RTPRegistryDefinitionSummary,
    RTPRegistryStatus,
    RTPRegistrySyncRun,
    RuntimePromptDefinition,
)
from app.models.oracle_artifact import OracleEnvironment
from app.services.business_rule_service import BusinessRuleService
from app.services.calc_manager_rtp_parser import (
    CalcManagerRTPParser,
    ParsedRuleRTPDefinition,
    definition_checksum,
)
from app.utils.exceptions import BusinessRuleError


class BusinessRuleRTPRegistryService:
    """Import, query, and enforce environment-specific RTP contracts."""

    def __init__(
        self,
        settings: Settings,
        *,
        parser: CalcManagerRTPParser | None = None,
    ) -> None:
        self._database = database_for(settings.database_target)
        self._environment = OracleEnvironment.from_settings(
            settings.epm_base_url,
            settings.application_name,
        )
        self._parser = parser or CalcManagerRTPParser()

    def import_package(self, source_name: str, content: bytes) -> RTPRegistryImportResult:
        """Atomically publish definitions parsed from one XML/ZIP package."""
        normalized_name = str(source_name).strip()
        source_checksum = hashlib.sha256(content).hexdigest()
        started_at = datetime.now(UTC)
        with self._database.begin() as connection:
            sync_run_id = int(
                connection.execute(
                    insert(business_rule_rtp_sync_runs)
                    .values(
                        environment_key=self._environment.key,
                        environment_base_url=self._environment.base_url,
                        application_name=self._environment.application_name,
                        source_name=normalized_name,
                        source_checksum=source_checksum,
                        parser_version=self._parser.PARSER_VERSION,
                        status="RUNNING",
                        rules_imported=0,
                        prompts_imported=0,
                        warnings=[],
                        started_at=started_at,
                    )
                    .returning(business_rule_rtp_sync_runs.c.sync_run_id)
                ).scalar_one()
            )

        try:
            parsed = self._parser.parse(normalized_name, content)
            completed_at = datetime.now(UTC)
            prompt_count = sum(len(item.prompts) for item in parsed.definitions)
            existing_checksums = self._active_definition_checksums()
            parsed_checksums = {
                item.rule_name.casefold(): definition_checksum(item)
                for item in parsed.definitions
            }
            rules_added = sum(
                name not in existing_checksums for name in parsed_checksums
            )
            rules_changed = sum(
                name in existing_checksums
                and existing_checksums[name] != checksum
                for name, checksum in parsed_checksums.items()
            )
            rules_unchanged = (
                len(parsed_checksums) - rules_added - rules_changed
            )
            with self._database.begin() as connection:
                for definition in parsed.definitions:
                    definition_id = self._upsert_definition(
                        connection,
                        definition,
                        sync_run_id=sync_run_id,
                        source_name=normalized_name,
                        source_checksum=source_checksum,
                        synchronized_at=completed_at,
                    )
                    connection.execute(
                        delete(business_rule_rtp_parameters).where(
                            business_rule_rtp_parameters.c.definition_id == definition_id
                        )
                    )
                    for prompt in sorted(
                        definition.prompts,
                        key=lambda item: item.prompt_order,
                    ):
                        connection.execute(
                            insert(business_rule_rtp_parameters).values(
                                definition_id=definition_id,
                                prompt_order=prompt.prompt_order,
                                name=prompt.name,
                                normalized_name=prompt.name.casefold(),
                                label=prompt.label,
                                value_type=prompt.value_type,
                                dimension=prompt.dimension,
                                default_value=prompt.default_value,
                                has_default=prompt.has_default,
                                required_at_launch=prompt.required_at_launch,
                                is_hidden=prompt.hidden,
                                allow_multiple=prompt.allow_multiple,
                                security_mode=prompt.security_mode,
                                scope_type=prompt.scope_type,
                                scope_name=prompt.scope_name,
                                source_variable_id=prompt.source_variable_id,
                                limit_type=prompt.limit_type,
                                limit_value=prompt.limit_value,
                                source_metadata=dict(prompt.source_metadata),
                            )
                        )
                connection.execute(
                    update(business_rule_rtp_sync_runs)
                    .where(business_rule_rtp_sync_runs.c.sync_run_id == sync_run_id)
                    .values(
                        status="COMPLETED",
                        rules_imported=len(parsed.definitions),
                        prompts_imported=prompt_count,
                        warnings=list(parsed.warnings),
                        completed_at=completed_at,
                    )
                )
        except Exception as exc:
            failed_at = datetime.now(UTC)
            with self._database.begin() as connection:
                connection.execute(
                    update(business_rule_rtp_sync_runs)
                    .where(business_rule_rtp_sync_runs.c.sync_run_id == sync_run_id)
                    .values(
                        status="FAILED",
                        error_summary=str(exc)[:2000],
                        completed_at=failed_at,
                    )
                )
            if isinstance(exc, BusinessRuleError):
                raise
            raise BusinessRuleError(f"Unable to import the RTP registry: {exc}") from exc

        return RTPRegistryImportResult(
            sync_run_id=sync_run_id,
            source_name=normalized_name,
            source_checksum=source_checksum,
            parser_version=self._parser.PARSER_VERSION,
            rules_imported=len(parsed.definitions),
            prompts_imported=prompt_count,
            warnings=parsed.warnings,
            completed_at=completed_at,
            rules_added=rules_added,
            rules_changed=rules_changed,
            rules_unchanged=rules_unchanged,
        )

    def status(
        self,
        *,
        live_rule_names: Sequence[str] | None = None,
        recent_limit: int = 10,
    ) -> RTPRegistryStatus:
        """Return current coverage without changing Oracle or registry data."""
        live_names = (
            tuple(
                sorted(
                    {
                        str(item).strip()
                        for item in live_rule_names
                        if str(item).strip()
                    },
                    key=str.casefold,
                )
            )
            if live_rule_names is not None
            else None
        )
        live_by_key = (
            {item.casefold(): item for item in live_names}
            if live_names is not None
            else None
        )
        with self._database.connect() as connection:
            rows = connection.execute(
                select(
                    business_rule_rtp_definitions.c.rule_name,
                    business_rule_rtp_definitions.c.normalized_rule_name,
                    business_rule_rtp_definitions.c.cube_name,
                    business_rule_rtp_definitions.c.source_name,
                    business_rule_rtp_definitions.c.synchronized_at,
                    func.count(
                        business_rule_rtp_parameters.c.parameter_id
                    ).label("prompt_count"),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    and_(
                                        business_rule_rtp_parameters.c.required_at_launch.is_(
                                            True
                                        ),
                                        business_rule_rtp_parameters.c.has_default.is_(
                                            False
                                        ),
                                    ),
                                    1,
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    ).label("required_prompt_count"),
                )
                .select_from(
                    business_rule_rtp_definitions.outerjoin(
                        business_rule_rtp_parameters,
                        business_rule_rtp_parameters.c.definition_id
                        == business_rule_rtp_definitions.c.definition_id,
                    )
                )
                .where(
                    business_rule_rtp_definitions.c.environment_key
                    == self._environment.key,
                    business_rule_rtp_definitions.c.is_active.is_(True),
                )
                .group_by(
                    business_rule_rtp_definitions.c.definition_id,
                    business_rule_rtp_definitions.c.rule_name,
                    business_rule_rtp_definitions.c.normalized_rule_name,
                    business_rule_rtp_definitions.c.cube_name,
                    business_rule_rtp_definitions.c.source_name,
                    business_rule_rtp_definitions.c.synchronized_at,
                )
                .order_by(func.lower(business_rule_rtp_definitions.c.rule_name))
            ).mappings().all()

        registered_by_key = {
            str(row["normalized_rule_name"]): str(row["rule_name"])
            for row in rows
        }
        unsynchronized = (
            tuple(
                live_by_key[key]
                for key in sorted(set(live_by_key) - set(registered_by_key))
            )
            if live_by_key is not None
            else ()
        )
        not_live = (
            tuple(
                registered_by_key[key]
                for key in sorted(set(registered_by_key) - set(live_by_key))
            )
            if live_by_key is not None
            else ()
        )
        definitions = tuple(
            RTPRegistryDefinitionSummary(
                rule_name=str(row["rule_name"]),
                cube_name=(
                    str(row["cube_name"]) if row["cube_name"] else None
                ),
                source_name=str(row["source_name"]),
                synchronized_at=utc_datetime(row["synchronized_at"]),
                prompt_count=int(row["prompt_count"] or 0),
                required_prompt_count=int(
                    row["required_prompt_count"] or 0
                ),
                live_status=(
                    "CATALOG_UNAVAILABLE"
                    if live_by_key is None
                    else "SYNCHRONIZED"
                    if str(row["normalized_rule_name"]) in live_by_key
                    else "NOT_IN_LIVE_CATALOG"
                ),
            )
            for row in rows
        )
        recent_syncs = self.recent_syncs(limit=recent_limit)
        latest_failed = bool(
            recent_syncs and recent_syncs[0].status == "FAILED"
        )
        if latest_failed:
            registry_status = "ATTENTION"
        elif not definitions:
            registry_status = "EMPTY"
        elif not_live:
            registry_status = "ATTENTION"
        elif live_by_key is None:
            registry_status = "CATALOG_UNAVAILABLE"
        else:
            registry_status = "HEALTHY"
        return RTPRegistryStatus(
            status=registry_status,
            application_name=self._environment.application_name,
            live_catalog_available=live_by_key is not None,
            live_rule_count=(len(live_names) if live_names is not None else None),
            synchronized_rule_count=len(definitions),
            synchronized_prompt_count=sum(
                item.prompt_count for item in definitions
            ),
            unsynchronized_live_rules=unsynchronized,
            definitions_not_in_live_catalog=not_live,
            definitions=definitions,
            recent_syncs=recent_syncs,
        )

    def recent_syncs(self, *, limit: int = 10) -> tuple[RTPRegistrySyncRun, ...]:
        """Return recent successful and failed imports for this environment."""
        safe_limit = max(1, min(int(limit), 50))
        with self._database.connect() as connection:
            rows = connection.execute(
                select(business_rule_rtp_sync_runs)
                .where(
                    business_rule_rtp_sync_runs.c.environment_key
                    == self._environment.key
                )
                .order_by(business_rule_rtp_sync_runs.c.started_at.desc())
                .limit(safe_limit)
            ).mappings().all()
        return tuple(
            RTPRegistrySyncRun(
                sync_run_id=int(row["sync_run_id"]),
                source_name=str(row["source_name"]),
                parser_version=str(row["parser_version"]),
                status=str(row["status"]),
                rules_imported=int(row["rules_imported"] or 0),
                prompts_imported=int(row["prompts_imported"] or 0),
                warnings=tuple(row["warnings"] or ()),
                error_summary=(
                    str(row["error_summary"])
                    if row["error_summary"]
                    else None
                ),
                started_at=utc_datetime(row["started_at"]),
                completed_at=(
                    utc_datetime(row["completed_at"])
                    if row["completed_at"] is not None
                    else None
                ),
            )
            for row in rows
        )

    def _active_definition_checksums(self) -> dict[str, str]:
        with self._database.connect() as connection:
            rows = connection.execute(
                select(
                    business_rule_rtp_definitions.c.normalized_rule_name,
                    business_rule_rtp_definitions.c.definition_checksum,
                ).where(
                    business_rule_rtp_definitions.c.environment_key
                    == self._environment.key,
                    business_rule_rtp_definitions.c.is_active.is_(True),
                )
            ).all()
        return {str(name): str(checksum) for name, checksum in rows}

    def get_definition(self, rule_name: str) -> BusinessRuleRTPDefinition | None:
        """Return the current RTP contract for an exact rule, case-insensitively."""
        normalized = BusinessRuleService.validate_rule_name(rule_name).casefold()
        with self._database.connect() as connection:
            row = connection.execute(
                select(business_rule_rtp_definitions).where(
                    business_rule_rtp_definitions.c.environment_key
                    == self._environment.key,
                    business_rule_rtp_definitions.c.normalized_rule_name == normalized,
                    business_rule_rtp_definitions.c.is_active.is_(True),
                )
            ).mappings().one_or_none()
            if row is None:
                return None
            prompt_rows = connection.execute(
                select(business_rule_rtp_parameters)
                .where(
                    business_rule_rtp_parameters.c.definition_id
                    == row["definition_id"]
                )
                .order_by(business_rule_rtp_parameters.c.prompt_order)
            ).mappings().all()
        return self._definition_from_rows(row, prompt_rows)

    def normalize_for_execution(
        self,
        rule_name: str,
        supplied: Mapping[str, str] | None,
    ) -> dict[str, str]:
        """Validate supplied values against a registry contract when available."""
        definition = self.get_definition(rule_name)
        if definition is None:
            return dict(BusinessRuleService.normalize_runtime_prompts(supplied))

        prompts_by_name = {item.name.casefold(): item for item in definition.prompts}
        normalized: dict[str, str] = {}
        for raw_name, raw_value in (supplied or {}).items():
            name = str(raw_name).strip()
            prompt = prompts_by_name.get(name.casefold())
            if prompt is None:
                raise BusinessRuleError(
                    f"Runtime prompt '{name}' is not registered for Business Rule "
                    f"'{definition.rule_name}'. Synchronize the Calc Manager export "
                    "if the rule definition changed."
                )
            value = str(raw_value).strip()
            if not value:
                raise BusinessRuleError(f"Runtime prompt '{prompt.name}' requires a value.")
            normalized[prompt.name] = value

        missing = [
            prompt.label
            for prompt in definition.prompts
            if prompt.required_at_launch
            and not prompt.has_default
            and prompt.name not in normalized
        ]
        if missing:
            raise BusinessRuleError(
                "Required runtime prompt values are missing for "
                f"'{definition.rule_name}': {', '.join(missing)}."
            )
        return normalized

    def latest_import(self) -> RTPRegistryImportResult | None:
        """Return the latest successful import for the connected environment."""
        with self._database.connect() as connection:
            row = connection.execute(
                select(business_rule_rtp_sync_runs)
                .where(
                    business_rule_rtp_sync_runs.c.environment_key == self._environment.key,
                    business_rule_rtp_sync_runs.c.status == "COMPLETED",
                )
                .order_by(business_rule_rtp_sync_runs.c.completed_at.desc())
                .limit(1)
            ).mappings().one_or_none()
        if row is None:
            return None
        return RTPRegistryImportResult(
            sync_run_id=int(row["sync_run_id"]),
            source_name=str(row["source_name"]),
            source_checksum=str(row["source_checksum"]),
            parser_version=str(row["parser_version"]),
            rules_imported=int(row["rules_imported"]),
            prompts_imported=int(row["prompts_imported"]),
            warnings=tuple(row["warnings"] or ()),
            completed_at=utc_datetime(row["completed_at"]),
        )

    def _upsert_definition(
        self,
        connection,
        definition: ParsedRuleRTPDefinition,
        *,
        sync_run_id: int,
        source_name: str,
        source_checksum: str,
        synchronized_at: datetime,
    ) -> int:
        values = {
            "environment_key": self._environment.key,
            "application_name": self._environment.application_name,
            "rule_name": definition.rule_name,
            "normalized_rule_name": definition.rule_name.casefold(),
            "cube_name": definition.cube_name,
            "source_name": source_name,
            "source_path": definition.source_path,
            "source_checksum": source_checksum,
            "parser_version": self._parser.PARSER_VERSION,
            "definition_checksum": definition_checksum(definition),
            "source_sync_run_id": sync_run_id,
            "is_active": True,
            "synchronized_at": synchronized_at,
        }
        connection.execute(
            upsert_statement(
                connection,
                business_rule_rtp_definitions,
                values,
                index_elements=("environment_key", "normalized_rule_name"),
                update_columns=(
                    "application_name",
                    "rule_name",
                    "cube_name",
                    "source_name",
                    "source_path",
                    "source_checksum",
                    "parser_version",
                    "definition_checksum",
                    "source_sync_run_id",
                    "is_active",
                    "synchronized_at",
                ),
            )
        )
        return int(
            connection.execute(
                select(business_rule_rtp_definitions.c.definition_id).where(
                    business_rule_rtp_definitions.c.environment_key
                    == self._environment.key,
                    business_rule_rtp_definitions.c.normalized_rule_name
                    == definition.rule_name.casefold(),
                )
            ).scalar_one()
        )

    @staticmethod
    def _definition_from_rows(row, prompt_rows) -> BusinessRuleRTPDefinition:
        return BusinessRuleRTPDefinition(
            definition_id=int(row["definition_id"]),
            environment_key=str(row["environment_key"]),
            application_name=str(row["application_name"]),
            rule_name=str(row["rule_name"]),
            cube_name=str(row["cube_name"]) if row["cube_name"] else None,
            source_name=str(row["source_name"]),
            source_checksum=str(row["source_checksum"]),
            parser_version=str(row["parser_version"]),
            definition_checksum=str(row["definition_checksum"]),
            synchronized_at=utc_datetime(row["synchronized_at"]),
            prompts=tuple(
                RuntimePromptDefinition(
                    name=str(item["name"]),
                    label=str(item["label"]),
                    prompt_order=int(item["prompt_order"]),
                    value_type=str(item["value_type"]),
                    dimension=str(item["dimension"]) if item["dimension"] else None,
                    default_value=(
                        str(item["default_value"])
                        if item["default_value"] is not None
                        else None
                    ),
                    has_default=bool(item["has_default"]),
                    required_at_launch=bool(item["required_at_launch"]),
                    hidden=bool(item["is_hidden"]),
                    allow_multiple=bool(item["allow_multiple"]),
                    security_mode=(
                        str(item["security_mode"])
                        if item["security_mode"]
                        else None
                    ),
                    scope_type=str(item["scope_type"] or "UNKNOWN"),
                    scope_name=(
                        str(item["scope_name"])
                        if item["scope_name"]
                        else None
                    ),
                    source_variable_id=(
                        str(item["source_variable_id"])
                        if item["source_variable_id"]
                        else None
                    ),
                    limit_type=(
                        str(item["limit_type"])
                        if item["limit_type"]
                        else None
                    ),
                    limit_value=(
                        str(item["limit_value"])
                        if item["limit_value"] is not None
                        else None
                    ),
                    source_metadata=dict(item["source_metadata"] or {}),
                )
                for item in prompt_rows
            ),
        )

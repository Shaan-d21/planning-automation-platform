"""Durable, environment-scoped Oracle artifact registrations."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update

from app.infrastructure.database.engine import (
    DatabaseTarget,
    database_for,
    upsert_statement,
    utc_datetime,
)
from app.infrastructure.database.schema import oracle_artifacts
from app.models.oracle_artifact import (
    OracleArtifact,
    OracleArtifactSource,
    OracleArtifactStatus,
    OracleArtifactType,
    OracleEnvironment,
)
from app.utils.exceptions import ConfigurationError


class OracleArtifactRegistry:
    """Persist artifact state without mixing Oracle applications or pods."""

    _LEGACY_ENVIRONMENT_KEY = "__legacy__"

    def __init__(
        self,
        database_target: DatabaseTarget,
        environment: OracleEnvironment,
    ) -> None:
        self._database = database_for(database_target)
        self.environment = environment
        self._adopt_legacy_registrations()

    def seed(
        self,
        artifact_type: OracleArtifactType,
        definitions: Iterable[tuple[str, str, str | None]],
        *,
        initial_status: OracleArtifactStatus = OracleArtifactStatus.PENDING,
    ) -> None:
        """Import configured seed definitions without overwriting lifecycle state."""
        now = datetime.now(UTC)
        with self._database.begin() as connection:
            for identifier, display_name, description in definitions:
                values = self._values(
                    artifact_type=artifact_type,
                    identifier=identifier,
                    display_name=display_name,
                    description=description,
                    source=OracleArtifactSource.SEED,
                    status=initial_status,
                    now=now,
                )
                connection.execute(
                    upsert_statement(
                        connection,
                        oracle_artifacts,
                        values,
                        index_elements=(
                            "environment_key",
                            "artifact_type",
                            "normalized_identifier",
                        ),
                        update_columns=(
                            "environment_base_url",
                            "application_name",
                        ),
                    )
                )

    def has_any_registrations(self) -> bool:
        """Return whether any environment has initialized the registry."""
        with self._database.connect() as connection:
            count = connection.execute(
                select(func.count()).select_from(oracle_artifacts)
            ).scalar_one()
        return int(count) > 0

    def has_environment_registrations(self) -> bool:
        """Return whether the connected environment already owns records."""
        with self._database.connect() as connection:
            count = connection.execute(
                select(func.count())
                .select_from(oracle_artifacts)
                .where(
                    oracle_artifacts.c.environment_key == self.environment.key
                )
            ).scalar_one()
        return int(count) > 0

    def register(
        self,
        artifact_type: OracleArtifactType,
        identifier: str,
        *,
        display_name: str | None = None,
        description: str | None = None,
        source: OracleArtifactSource = OracleArtifactSource.MANUAL,
        status: OracleArtifactStatus = OracleArtifactStatus.PENDING,
    ) -> OracleArtifact:
        """Create or update one registration while preserving its identity."""
        normalized_identifier = self._normalize_identifier(identifier)
        now = datetime.now(UTC)
        values = self._values(
            artifact_type=artifact_type,
            identifier=normalized_identifier,
            display_name=display_name or normalized_identifier,
            description=description,
            source=source,
            status=status,
            now=now,
        )
        if status == OracleArtifactStatus.VERIFIED:
            values["last_verified_at"] = now
        with self._database.begin() as connection:
            connection.execute(
                upsert_statement(
                    connection,
                    oracle_artifacts,
                    values,
                    index_elements=(
                        "environment_key",
                        "artifact_type",
                        "normalized_identifier",
                    ),
                    update_columns=(
                        "environment_base_url",
                        "application_name",
                        "oracle_identifier",
                        "display_name",
                        "description",
                        "source",
                        "verification_status",
                        "is_active",
                        "consecutive_missing_count",
                        "last_verified_at",
                        "last_error",
                        "updated_at",
                    ),
                )
            )
        return self.require(artifact_type, normalized_identifier)

    def list(
        self,
        artifact_type: OracleArtifactType,
        *,
        include_inactive: bool = False,
    ) -> tuple[OracleArtifact, ...]:
        statement = select(oracle_artifacts).where(
            oracle_artifacts.c.environment_key == self.environment.key,
            oracle_artifacts.c.artifact_type == artifact_type.value,
        )
        if not include_inactive:
            statement = statement.where(oracle_artifacts.c.is_active.is_(True))
        statement = statement.order_by(
            func.lower(oracle_artifacts.c.display_name),
            func.lower(oracle_artifacts.c.oracle_identifier),
        )
        with self._database.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return tuple(self._from_row(row) for row in rows)

    def list_all(
        self,
        *,
        include_inactive: bool = False,
    ) -> tuple[OracleArtifact, ...]:
        """Return every catalog entry for the connected Oracle environment."""
        statement = select(oracle_artifacts).where(
            oracle_artifacts.c.environment_key == self.environment.key
        )
        if not include_inactive:
            statement = statement.where(oracle_artifacts.c.is_active.is_(True))
        statement = statement.order_by(
            oracle_artifacts.c.artifact_type,
            func.lower(oracle_artifacts.c.display_name),
        )
        with self._database.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return tuple(self._from_row(row) for row in rows)

    def reconcile_snapshot(
        self,
        artifact_type: OracleArtifactType,
        identifiers: Iterable[str],
        *,
        description: str | None = None,
    ) -> tuple[OracleArtifact, ...]:
        """Apply one authoritative, read-only Oracle discovery snapshot.

        Newly discovered entries are verified immediately. Entries previously
        created by live discovery are hidden after the first missing snapshot
        and softly deactivated after the second, preserving their audit trail.
        """
        discovered: dict[str, str] = {}
        for identifier in identifiers:
            normalized = self._normalize_identifier(identifier)
            discovered.setdefault(normalized.casefold(), normalized)

        for identifier in discovered.values():
            self.mark_verified(
                artifact_type,
                identifier,
                display_name=identifier,
                description=description,
                source=OracleArtifactSource.LIVE_DISCOVERY,
            )

        for artifact in self.list(artifact_type, include_inactive=True):
            if artifact.source != OracleArtifactSource.LIVE_DISCOVERY:
                continue
            if artifact.oracle_identifier.casefold() in discovered:
                continue
            self.mark_missing(
                artifact_type,
                artifact.oracle_identifier,
                "The artifact was not returned by the latest authoritative "
                "Oracle discovery.",
            )

        return self.list(artifact_type, include_inactive=True)

    def get(
        self,
        artifact_type: OracleArtifactType,
        identifier: str,
    ) -> OracleArtifact | None:
        normalized = self._normalize_identifier(identifier).casefold()
        with self._database.connect() as connection:
            row = connection.execute(
                select(oracle_artifacts).where(
                    oracle_artifacts.c.environment_key == self.environment.key,
                    oracle_artifacts.c.artifact_type == artifact_type.value,
                    oracle_artifacts.c.normalized_identifier == normalized,
                )
            ).mappings().one_or_none()
        return self._from_row(row) if row is not None else None

    def require(
        self,
        artifact_type: OracleArtifactType,
        identifier: str,
    ) -> OracleArtifact:
        artifact = self.get(artifact_type, identifier)
        if artifact is None:
            label = artifact_type.value.replace("_", " ").title()
            raise ConfigurationError(
                f"{label} '{str(identifier).strip()}' is not registered for "
                f"Oracle application '{self.environment.application_name}'."
            )
        return artifact

    def require_runnable(
        self,
        artifact_type: OracleArtifactType,
        identifier: str,
    ) -> OracleArtifact:
        artifact = self.require(artifact_type, identifier)
        if not artifact.is_runnable:
            label = artifact_type.value.replace("_", " ").title()
            raise ConfigurationError(
                f"{label} '{artifact.oracle_identifier}' is "
                f"{artifact.status.value.lower()} in the connected Oracle "
                "application. Synchronize or register it again before running."
            )
        return artifact

    def mark_verified(
        self,
        artifact_type: OracleArtifactType,
        identifier: str,
        *,
        display_name: str | None = None,
        description: str | None = None,
        source: OracleArtifactSource | None = None,
    ) -> OracleArtifact:
        """Record an authoritative successful Oracle lookup or submission."""
        artifact = self.get(artifact_type, identifier)
        if artifact is None:
            return self.register(
                artifact_type,
                identifier,
                display_name=display_name,
                description=description,
                source=source or OracleArtifactSource.MANUAL,
                status=OracleArtifactStatus.VERIFIED,
            )
        now = datetime.now(UTC)
        values: dict = {
            "verification_status": OracleArtifactStatus.VERIFIED.value,
            "is_active": True,
            "consecutive_missing_count": 0,
            "last_verified_at": now,
            "last_error": None,
            "updated_at": now,
        }
        if display_name:
            values["display_name"] = display_name.strip()
        if description is not None:
            values["description"] = description.strip() or None
        if source is not None:
            values["source"] = source.value
        with self._database.begin() as connection:
            connection.execute(
                update(oracle_artifacts)
                .where(oracle_artifacts.c.artifact_id == artifact.artifact_id)
                .values(**values)
            )
        return self.require(artifact_type, identifier)

    def mark_missing(
        self,
        artifact_type: OracleArtifactType,
        identifier: str,
        error: str,
    ) -> OracleArtifact:
        """Hide once missing and deactivate after two authoritative misses."""
        artifact = self.require(artifact_type, identifier)
        count = artifact.consecutive_missing_count + 1
        inactive = count >= 2
        now = datetime.now(UTC)
        with self._database.begin() as connection:
            connection.execute(
                update(oracle_artifacts)
                .where(oracle_artifacts.c.artifact_id == artifact.artifact_id)
                .values(
                    verification_status=(
                        OracleArtifactStatus.INACTIVE.value
                        if inactive
                        else OracleArtifactStatus.MISSING.value
                    ),
                    is_active=not inactive,
                    consecutive_missing_count=count,
                    last_error=self._bounded_error(error),
                    updated_at=now,
                )
            )
        return self.require(artifact_type, identifier)

    def record_verification_error(
        self,
        artifact_type: OracleArtifactType,
        identifier: str,
        error: str,
    ) -> None:
        """Retain lifecycle state when Oracle is unavailable or ambiguous."""
        artifact = self.get(artifact_type, identifier)
        if artifact is None:
            return
        with self._database.begin() as connection:
            connection.execute(
                update(oracle_artifacts)
                .where(oracle_artifacts.c.artifact_id == artifact.artifact_id)
                .values(
                    last_error=self._bounded_error(error),
                    updated_at=datetime.now(UTC),
                )
            )

    def _adopt_legacy_registrations(self) -> None:
        """Assign pre-environment registrations to the first upgraded runtime."""
        with self._database.begin() as connection:
            legacy_rows = connection.execute(
                select(oracle_artifacts).where(
                    oracle_artifacts.c.environment_key
                    == self._LEGACY_ENVIRONMENT_KEY
                )
            ).mappings().all()
            if not legacy_rows:
                return
            now = datetime.now(UTC)
            for row in legacy_rows:
                values = self._values(
                    artifact_type=OracleArtifactType(str(row["artifact_type"])),
                    identifier=str(row["oracle_identifier"]),
                    display_name=str(row["display_name"]),
                    description=(
                        str(row["description"])
                        if row["description"] is not None
                        else None
                    ),
                    source=OracleArtifactSource.LEGACY,
                    status=OracleArtifactStatus.PENDING,
                    now=now,
                )
                connection.execute(
                    upsert_statement(
                        connection,
                        oracle_artifacts,
                        values,
                        index_elements=(
                            "environment_key",
                            "artifact_type",
                            "normalized_identifier",
                        ),
                        update_columns=(
                            "display_name",
                            "description",
                            "updated_at",
                        ),
                    )
                )
            connection.execute(
                delete(oracle_artifacts).where(
                    oracle_artifacts.c.environment_key
                    == self._LEGACY_ENVIRONMENT_KEY
                )
            )

    def _values(
        self,
        *,
        artifact_type: OracleArtifactType,
        identifier: str,
        display_name: str,
        description: str | None,
        source: OracleArtifactSource,
        status: OracleArtifactStatus,
        now: datetime,
    ) -> dict[str, object]:
        normalized = self._normalize_identifier(identifier)
        return {
            "environment_key": self.environment.key,
            "environment_base_url": self.environment.base_url,
            "application_name": self.environment.application_name,
            "artifact_type": artifact_type.value,
            "oracle_identifier": normalized,
            "normalized_identifier": normalized.casefold(),
            "display_name": str(display_name).strip() or normalized,
            "description": (
                str(description).strip() or None
                if description is not None
                else None
            ),
            "source": source.value,
            "verification_status": status.value,
            "is_active": True,
            "consecutive_missing_count": 0,
            "last_verified_at": None,
            "last_error": None,
            "created_at": now,
            "updated_at": now,
        }

    @staticmethod
    def _normalize_identifier(identifier: str) -> str:
        normalized = str(identifier).strip()
        if not normalized:
            raise ConfigurationError("Oracle artifact identifier cannot be empty.")
        return normalized

    @staticmethod
    def _bounded_error(error: str) -> str:
        return " ".join(str(error).split())[:2_000]

    @staticmethod
    def _from_row(row) -> OracleArtifact:
        return OracleArtifact(
            artifact_id=int(row["artifact_id"]),
            environment_key=str(row["environment_key"]),
            artifact_type=OracleArtifactType(str(row["artifact_type"])),
            oracle_identifier=str(row["oracle_identifier"]),
            display_name=str(row["display_name"]),
            description=(
                str(row["description"])
                if row["description"] is not None
                else None
            ),
            source=OracleArtifactSource(str(row["source"])),
            status=OracleArtifactStatus(str(row["verification_status"])),
            is_active=bool(row["is_active"]),
            consecutive_missing_count=int(row["consecutive_missing_count"]),
            last_verified_at=utc_datetime(row["last_verified_at"]),
            last_error=(
                str(row["last_error"])
                if row["last_error"] is not None
                else None
            ),
            created_at=utc_datetime(row["created_at"]),
            updated_at=utc_datetime(row["updated_at"]),
        )

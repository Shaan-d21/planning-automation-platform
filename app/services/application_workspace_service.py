"""Registered Oracle applications, memberships, and session workspace state."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import and_, insert, select, update

from app.infrastructure.database.engine import DatabaseTarget, database_for
from app.infrastructure.database.schema import (
    oracle_applications,
    platform_sessions,
    platform_user_applications,
)
from app.models.application_workspace import ApplicationWorkspace
from app.models.environment_configuration import EnvironmentConfiguration
from app.products.context import classify_business_process
from app.products.contracts import BusinessProcessType
from app.services.session_security_service import SessionSecurityService
from app.utils.exceptions import ConfigurationError


class ApplicationWorkspaceService:
    """Maintain the non-secret application registry and isolation boundary.

    This service deliberately does not build Oracle clients or change runtime
    settings. Request-scoped Oracle routing is a separate release gate.
    """

    def __init__(self, database_target: DatabaseTarget) -> None:
        self._database = database_for(database_target)

    def synchronize(
        self,
        configuration: EnvironmentConfiguration | None,
        *,
        actor_user_id: int | None = None,
    ) -> tuple[ApplicationWorkspace, ...]:
        """Upsert applications verified for the configured Oracle endpoint."""

        if configuration is None:
            return ()
        now = datetime.now(UTC)
        applications = list(configuration.applications)
        selected = configuration.selected_application_info
        if (
            selected is None
            and configuration.selected_application
            and not applications
        ):
            # Compatibility registration for deployments that started from
            # APPLICATION_NAME before live discovery was available.
            from app.models.environment import ApplicationInfo

            applications.append(
                ApplicationInfo(name=configuration.selected_application)
            )

        with self._database.begin() as connection:
            existing = {
                str(row["application_name"]).casefold(): row
                for row in connection.execute(
                    select(oracle_applications).where(
                        oracle_applications.c.environment_base_url
                        == configuration.base_url
                    )
                ).mappings()
            }
            seen: set[int] = set()
            for application in applications:
                business_process = classify_business_process(
                    product_type=application.product_type,
                    application_type=application.application_type,
                )
                if (
                    business_process is BusinessProcessType.UNKNOWN
                    and configuration.selected_application
                    and application.name.casefold()
                    == configuration.selected_application.casefold()
                ):
                    business_process = (
                        BusinessProcessType.PLANNING
                        if configuration.selection_source == "ENVIRONMENT"
                        and configuration.selected_business_process
                        is BusinessProcessType.UNKNOWN
                        else configuration.selected_business_process
                    )
                values = {
                    "product_type": application.product_type,
                    "application_type": application.application_type,
                    "business_process": business_process.value,
                    "is_active": True,
                    "last_verified_at": configuration.last_discovered_at,
                    "updated_at": now,
                }
                current = existing.get(application.name.casefold())
                if current is None:
                    result = connection.execute(
                        insert(oracle_applications).values(
                            environment_base_url=configuration.base_url,
                            application_name=application.name,
                            created_at=now,
                            **values,
                        )
                    )
                    application_id = int(result.inserted_primary_key[0])
                else:
                    application_id = int(current["application_id"])
                    connection.execute(
                        update(oracle_applications)
                        .where(
                            oracle_applications.c.application_id
                            == application_id
                        )
                        .values(**values)
                    )
                seen.add(application_id)
                if actor_user_id is not None:
                    self._grant(
                        connection,
                        user_id=actor_user_id,
                        application_id=application_id,
                        granted_by_user_id=actor_user_id,
                        now=now,
                    )

            if applications:
                for row in existing.values():
                    application_id = int(row["application_id"])
                    if application_id not in seen:
                        connection.execute(
                            update(oracle_applications)
                            .where(
                                oracle_applications.c.application_id
                                == application_id
                            )
                            .values(is_active=False, updated_at=now)
                        )

        return self.registered_for_environment(configuration.base_url)

    def establish_default_for_session(
        self,
        raw_session_id: str,
        *,
        user_id: int,
        configuration: EnvironmentConfiguration | None,
    ) -> ApplicationWorkspace | None:
        """Preserve existing access and bind a new session to its default app."""

        if configuration is None or not configuration.selected_application:
            return None
        self.synchronize(configuration)
        now = datetime.now(UTC)
        with self._database.begin() as connection:
            application = connection.execute(
                select(oracle_applications).where(
                    oracle_applications.c.environment_base_url
                    == configuration.base_url,
                    oracle_applications.c.application_name
                    == configuration.selected_application,
                    oracle_applications.c.is_active.is_(True),
                )
            ).mappings().one_or_none()
            if application is None:
                return None
            application_id = int(application["application_id"])
            self._grant(
                connection,
                user_id=user_id,
                application_id=application_id,
                granted_by_user_id=None,
                now=now,
            )
            connection.execute(
                update(platform_sessions)
                .where(
                    platform_sessions.c.session_id_hash
                    == SessionSecurityService.session_key(raw_session_id),
                    platform_sessions.c.user_id == user_id,
                )
                .values(active_application_id=application_id)
            )
        return self.current_for_session(raw_session_id, user_id=user_id)

    def registered_for_environment(
        self,
        environment_base_url: str,
    ) -> tuple[ApplicationWorkspace, ...]:
        """Return the safe registry for one configured Oracle endpoint."""

        with self._database.connect() as connection:
            rows = connection.execute(
                select(oracle_applications)
                .where(
                    oracle_applications.c.environment_base_url
                    == environment_base_url
                )
                .order_by(oracle_applications.c.application_name)
            ).mappings().all()
        return tuple(self._workspace(row) for row in rows)

    def available_for_user(
        self,
        user_id: int,
        *,
        raw_session_id: str | None = None,
    ) -> tuple[ApplicationWorkspace, ...]:
        """Return only active applications explicitly assigned to one user."""

        current_id: int | None = None
        session_key = (
            SessionSecurityService.session_key(raw_session_id)
            if raw_session_id
            else None
        )
        with self._database.connect() as connection:
            if session_key:
                current_id = connection.execute(
                    select(platform_sessions.c.active_application_id).where(
                        platform_sessions.c.session_id_hash == session_key,
                        platform_sessions.c.user_id == user_id,
                    )
                ).scalar_one_or_none()
            rows = connection.execute(
                select(oracle_applications)
                .join(
                    platform_user_applications,
                    platform_user_applications.c.application_id
                    == oracle_applications.c.application_id,
                )
                .where(
                    platform_user_applications.c.user_id == user_id,
                    oracle_applications.c.is_active.is_(True),
                )
                .order_by(oracle_applications.c.application_name)
            ).mappings().all()
        return tuple(
            self._workspace(row, current=int(row["application_id"]) == current_id)
            for row in rows
        )

    def current_for_session(
        self,
        raw_session_id: str,
        *,
        user_id: int,
        environment_base_url: str | None = None,
    ) -> ApplicationWorkspace | None:
        """Resolve the active application within an optional environment boundary."""

        conditions = [
            platform_sessions.c.session_id_hash
            == SessionSecurityService.session_key(raw_session_id),
            platform_sessions.c.user_id == user_id,
            oracle_applications.c.is_active.is_(True),
        ]
        if environment_base_url is not None:
            conditions.append(
                oracle_applications.c.environment_base_url
                == environment_base_url
            )

        with self._database.connect() as connection:
            row = connection.execute(
                select(oracle_applications)
                .join(
                    platform_sessions,
                    platform_sessions.c.active_application_id
                    == oracle_applications.c.application_id,
                )
                .join(
                    platform_user_applications,
                    and_(
                        platform_user_applications.c.application_id
                        == oracle_applications.c.application_id,
                        platform_user_applications.c.user_id == user_id,
                    ),
                )
                .where(*conditions)
            ).mappings().one_or_none()
        return self._workspace(row, current=True) if row is not None else None

    def select_for_session(
        self,
        raw_session_id: str,
        *,
        user_id: int,
        application_id: int,
    ) -> ApplicationWorkspace:
        """Persist an authorized session selection for request routing.

        The public switch endpoint is intentionally deferred until Oracle
        clients and product composition are request-scoped.
        """

        with self._database.begin() as connection:
            allowed = connection.execute(
                select(oracle_applications.c.application_id).join(
                    platform_user_applications,
                    and_(
                        platform_user_applications.c.application_id
                        == oracle_applications.c.application_id,
                        platform_user_applications.c.user_id == user_id,
                    ),
                ).where(
                    oracle_applications.c.application_id == application_id,
                    oracle_applications.c.is_active.is_(True),
                )
            ).scalar_one_or_none()
            if allowed is None:
                raise ConfigurationError(
                    "The selected Oracle application is not assigned to this user."
                )
            result = connection.execute(
                update(platform_sessions)
                .where(
                    platform_sessions.c.session_id_hash
                    == SessionSecurityService.session_key(raw_session_id),
                    platform_sessions.c.user_id == user_id,
                    platform_sessions.c.ended_at.is_(None),
                    platform_sessions.c.revoked_at.is_(None),
                )
                .values(active_application_id=application_id)
            )
            if not result.rowcount:
                raise ConfigurationError("The current platform session is invalid.")
        workspace = self.current_for_session(raw_session_id, user_id=user_id)
        if workspace is None:
            raise ConfigurationError("The application workspace could not be selected.")
        return workspace

    @staticmethod
    def _grant(
        connection,
        *,
        user_id: int,
        application_id: int,
        granted_by_user_id: int | None,
        now: datetime,
    ) -> None:
        exists = connection.execute(
            select(platform_user_applications.c.user_id).where(
                platform_user_applications.c.user_id == user_id,
                platform_user_applications.c.application_id == application_id,
            )
        ).scalar_one_or_none()
        if exists is None:
            connection.execute(
                insert(platform_user_applications).values(
                    user_id=user_id,
                    application_id=application_id,
                    granted_at=now,
                    granted_by_user_id=granted_by_user_id,
                )
            )

    @staticmethod
    def _workspace(row, *, current: bool = False) -> ApplicationWorkspace:
        try:
            business_process = BusinessProcessType(
                str(row["business_process"]).upper()
            )
        except ValueError:
            business_process = BusinessProcessType.UNKNOWN
        return ApplicationWorkspace(
            application_id=int(row["application_id"]),
            application_name=str(row["application_name"]),
            business_process=business_process,
            product_type=(
                str(row["product_type"])
                if row.get("product_type") is not None
                else None
            ),
            application_type=(
                str(row["application_type"])
                if row.get("application_type") is not None
                else None
            ),
            active=bool(row["is_active"]),
            last_verified_at=row.get("last_verified_at"),
            current=current,
        )

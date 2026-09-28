"""Environment-scoped Oracle identity selection for user variables."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import insert

from app.config.settings import Settings
from app.infrastructure.database.engine import database_for
from app.infrastructure.database.schema import (
    external_identities,
    identity_providers,
)
from app.models.oracle_artifact import OracleEnvironment
from app.services.access_control_service import AccessControlService
from app.services.oracle_user_variable_identity import (
    OracleUserVariableIdentityResolver,
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        epm_base_url="https://example.epm.oraclecloud.com",
        epm_username="epm.service@example.com",
        epm_password="secret",
        application_name="Planning",
        workflow_database_file=tmp_path / "identity.sqlite3",
    )


def _platform_user(settings: Settings):
    return AccessControlService(
        settings.database_target
    ).bootstrap_administrator(
        username="local.admin",
        display_name="Local Administrator",
        email="local.admin@platform.test",
        password="a secure administrator password",
    )


def _link_oracle_identity(
    settings: Settings,
    user_id: int,
    *,
    username: str,
    environment_key: str,
) -> None:
    now = datetime.now(UTC)
    database = database_for(settings.database_target)
    with database.begin() as connection:
        result = connection.execute(
            insert(identity_providers).values(
                code=f"oracle-{environment_key[:16]}",
                provider_type="ORACLE_CLOUD",
                display_name="Oracle EPM",
                environment_key=environment_key,
                is_enabled=True,
                safe_configuration={},
                created_at=now,
                updated_at=now,
            )
        )
        provider_id = result.inserted_primary_key[0]
        connection.execute(
            insert(external_identities).values(
                provider_id=provider_id,
                user_id=user_id,
                subject=f"epm-login:{username.casefold()}",
                username=username,
                display_name="Oracle Planner",
                is_active=True,
                last_synced_at=now,
                created_at=now,
                updated_at=now,
            )
        )


def test_local_platform_user_falls_back_to_service_account(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    user = _platform_user(settings)

    resolved = OracleUserVariableIdentityResolver(settings).resolve(user)

    assert resolved == "epm.service@example.com"


def test_oracle_linked_user_uses_environment_identity(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    user = _platform_user(settings)
    environment = OracleEnvironment.from_settings(
        settings.epm_base_url,
        settings.application_name,
    )
    _link_oracle_identity(
        settings,
        user.user_id,
        username="planner@example.com",
        environment_key=environment.key,
    )

    resolved = OracleUserVariableIdentityResolver(settings).resolve(user)

    assert resolved == "planner@example.com"


def test_identity_from_another_environment_is_not_reused(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    user = _platform_user(settings)
    _link_oracle_identity(
        settings,
        user.user_id,
        username="other.environment@example.com",
        environment_key="0" * 64,
    )

    resolved = OracleUserVariableIdentityResolver(settings).resolve(user)

    assert resolved == "epm.service@example.com"

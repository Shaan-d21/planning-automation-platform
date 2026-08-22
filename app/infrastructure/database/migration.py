"""Production schema revision checks for safe application startup."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from app.infrastructure.database.engine import DatabaseTarget, database_for
from app.utils.exceptions import ConfigurationError, DatabaseConnectionError


def assert_schema_current(
    database_target: DatabaseTarget,
    *,
    project_root: Path,
) -> None:
    """Fail startup when PostgreSQL has not been migrated to the code head."""
    if isinstance(database_target, Path):
        return
    config = Config(str(project_root / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)
    expected = set(scripts.get_heads())
    try:
        with database_for(database_target).connect() as connection:
            current = set(
                MigrationContext.configure(connection).get_current_heads()
            )
    except OperationalError as exc:
        raise DatabaseConnectionError(
            "Could not connect to the PostgreSQL platform database. Verify "
            "that PostgreSQL is running and that DATABASE_URL contains the "
            "correct host, port, database, username, and URL-encoded password."
        ) from exc
    except SQLAlchemyError as exc:
        raise DatabaseConnectionError(
            "Could not inspect the PostgreSQL platform database. Verify "
            "DATABASE_URL and the database user's connection permissions."
        ) from exc
    if current != expected:
        installed = ", ".join(sorted(current)) or "unversioned"
        required = ", ".join(sorted(expected))
        raise ConfigurationError(
            "PostgreSQL schema is not current. "
            f"Installed revision: {installed}; required: {required}. "
            "Run 'alembic upgrade head' before starting the application."
        )

"""Database engine, connection pooling, and transaction boundaries."""

from __future__ import annotations

from functools import lru_cache
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeAlias

from sqlalchemy import Engine, Table, create_engine, event
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.infrastructure.database.schema import metadata
from app.utils.exceptions import ConfigurationError


DatabaseTarget: TypeAlias = str | Path


def utc_datetime(value: datetime | None) -> datetime | None:
    """Normalize test-dialect naive timestamps to aware UTC values."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


class Database:
    """Own one SQLAlchemy engine and explicit transaction boundaries."""

    def __init__(self, url: str, *, create_test_schema: bool = False) -> None:
        parsed = make_url(url)
        if parsed.get_backend_name() not in {"postgresql", "sqlite"}:
            raise ConfigurationError(
                "DATABASE_URL must use PostgreSQL. SQLite is supported only "
                "by isolated automated tests."
            )
        engine_options: dict[str, object] = {
            "pool_pre_ping": True,
            "future": True,
        }
        if parsed.get_backend_name() == "postgresql":
            engine_options.update(
                pool_size=10,
                max_overflow=20,
                pool_timeout=30,
                pool_recycle=1_800,
            )
        self._engine = create_engine(url, **engine_options)
        if parsed.get_backend_name() == "sqlite":
            @event.listens_for(self._engine, "connect")
            def _enable_sqlite_foreign_keys(dbapi_connection, _):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()
        if create_test_schema:
            metadata.create_all(self._engine)

    @property
    def engine(self) -> Engine:
        """Return the shared SQLAlchemy engine."""
        return self._engine

    def begin(self):
        """Return a context manager for one atomic transaction."""
        return self._engine.begin()

    def connect(self) -> Connection:
        """Open a read-oriented connection owned by the caller."""
        return self._engine.connect()


def _normalized_target(target: DatabaseTarget) -> tuple[str, bool]:
    if isinstance(target, Path):
        path = target.expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+pysqlite:///{path.as_posix()}", True
    value = str(target).strip()
    if not value:
        raise ConfigurationError("DATABASE_URL cannot be empty.")
    parsed = make_url(value)
    if parsed.get_backend_name() == "sqlite":
        raise ConfigurationError(
            "SQLite URLs are not supported for application runtime. Pass a "
            "Path only from isolated tests or configure PostgreSQL."
        )
    return value, False


@lru_cache(maxsize=32)
def _database_for_url(url: str, create_test_schema: bool) -> Database:
    return Database(url, create_test_schema=create_test_schema)


def database_for(target: DatabaseTarget) -> Database:
    """Resolve one cached database; `Path` is a test-only compatibility hook."""
    url, create_test_schema = _normalized_target(target)
    return _database_for_url(url, create_test_schema)


def upsert_statement(
    connection: Connection,
    table: Table,
    values: dict[str, object],
    *,
    index_elements: tuple[str, ...],
    update_columns: tuple[str, ...],
):
    """Build a native PostgreSQL/SQLite upsert for repository adapters."""
    insert_factory = (
        postgresql_insert
        if connection.dialect.name == "postgresql"
        else sqlite_insert
    )
    statement = insert_factory(table).values(**values)
    return statement.on_conflict_do_update(
        index_elements=list(index_elements),
        set_={name: getattr(statement.excluded, name) for name in update_columns},
    )

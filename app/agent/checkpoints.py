"""Durable LangGraph checkpoint lifecycle for test and production runtimes."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url

from app.infrastructure.database.engine import DatabaseTarget
from app.utils.exceptions import AgentConfigurationError


class AgentCheckpointStore:
    """Own a thread-safe checkpoint backend for the agent graph."""

    def __init__(
        self,
        database_target: DatabaseTarget,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._database_target = database_target
        self._logger = logger or logging.getLogger(__name__)
        self._lock = threading.Lock()
        self._checkpointer: Any | None = None
        self._pool: Any | None = None

    def get(self):
        """Return the shared saver, opening its connection pool once."""
        if self._checkpointer is not None:
            return self._checkpointer
        with self._lock:
            if self._checkpointer is not None:
                return self._checkpointer
            if isinstance(self._database_target, Path):
                from langgraph.checkpoint.memory import InMemorySaver

                self._checkpointer = InMemorySaver()
                return self._checkpointer
            try:
                from langgraph.checkpoint.postgres import PostgresSaver
                from psycopg_pool import ConnectionPool
            except ImportError as exc:
                raise AgentConfigurationError(
                    "LangGraph PostgreSQL checkpoint support is not installed. "
                    "Run 'pip install -r requirements.txt'."
                ) from exc
            connection_url = self._psycopg_url(str(self._database_target))
            self._pool = ConnectionPool(
                conninfo=connection_url,
                min_size=1,
                max_size=10,
                open=True,
                kwargs={"autocommit": True, "prepare_threshold": 0},
            )
            self._checkpointer = PostgresSaver(self._pool)
            return self._checkpointer

    def setup(self) -> None:
        """Create or upgrade the LangGraph-owned checkpoint tables."""
        if isinstance(self._database_target, Path):
            self.get()
            return
        checkpointer = self.get()
        try:
            checkpointer.setup()
        except Exception as exc:
            raise AgentConfigurationError(
                "LangGraph checkpoint setup failed. Verify DATABASE_URL and "
                "the PostgreSQL user's schema-creation permissions."
            ) from exc
        self._logger.info("LangGraph PostgreSQL checkpoint schema is ready.")

    def close(self) -> None:
        """Close the production connection pool."""
        with self._lock:
            pool = self._pool
            self._pool = None
            self._checkpointer = None
        if pool is not None:
            pool.close()

    @staticmethod
    def _psycopg_url(database_url: str) -> str:
        """Remove SQLAlchemy's driver suffix for psycopg_pool."""
        parsed = make_url(database_url)
        if parsed.get_backend_name() != "postgresql":
            raise AgentConfigurationError(
                "LangGraph durable checkpoints require PostgreSQL."
            )
        return parsed.set(drivername="postgresql").render_as_string(
            hide_password=False
        )

"""SQLAlchemy database infrastructure for platform persistence."""

from app.infrastructure.database.engine import Database, database_for

__all__ = ["Database", "database_for"]

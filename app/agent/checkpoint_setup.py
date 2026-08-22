"""Deployment command for the LangGraph-owned PostgreSQL checkpoint schema."""

from __future__ import annotations

from app.agent.checkpoints import AgentCheckpointStore
from app.config.settings import Settings
from app.utils.logger import configure_logging


def main() -> int:
    """Create or upgrade checkpoint tables after Alembic migrations."""
    configure_logging()
    settings = Settings.from_env()
    store = AgentCheckpointStore(settings.database_target)
    try:
        store.setup()
    finally:
        store.close()
    print("LangGraph checkpoint schema is ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

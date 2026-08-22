"""Dedicated durable execution and schedule worker entry point."""

from __future__ import annotations

import logging
import signal
from dataclasses import replace

from app.application.execution_manager import PlanningProcessExecutionManager
from app.application.execution_worker import DurableExecutionWorker
from app.application.planning_process import PlanningProcessApplicationService
from app.application.process_designer import ProcessDesignerApplicationService
from app.application.schedule_manager import ProcessScheduleManager
from app.application.scheduling import ProcessScheduleApplicationService
from app.config.settings import PROJECT_ROOT, Settings
from app.infrastructure.database.migration import assert_schema_current
from app.utils.logger import configure_logging


def main() -> int:
    """Run the worker until the operating system requests shutdown."""
    settings = replace(Settings.from_env(), execution_runtime="worker")
    logger = configure_logging(settings.log_level).getChild("worker")
    assert_schema_current(settings.database_target, project_root=PROJECT_ROOT)

    process_manager = PlanningProcessExecutionManager(
        settings,
        logger=logger.getChild("process_dispatch"),
    )
    process_service = PlanningProcessApplicationService(settings)
    process_designer = ProcessDesignerApplicationService(settings)
    schedule_service = ProcessScheduleApplicationService(
        settings,
        process_service=process_service,
        process_designer=process_designer,
        execution_manager=process_manager,
        logger=logger.getChild("schedules"),
    )
    schedule_manager = ProcessScheduleManager(
        schedule_service,
        poll_interval=settings.schedule_poll_interval,
        logger=logger.getChild("schedule_manager"),
    )
    worker = DurableExecutionWorker(
        settings,
        logger=logger.getChild("execution"),
    )

    def shutdown(*_: object) -> None:
        logger.info("Worker shutdown requested.")
        worker.shutdown()

    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)

    schedule_manager.start()
    try:
        worker.run_forever()
    finally:
        schedule_manager.shutdown()
        process_manager.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

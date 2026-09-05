"""Dedicated durable execution and schedule worker entry point."""

from __future__ import annotations

import logging
import signal
from dataclasses import replace

from app.application.execution_worker import DurableExecutionWorker
from app.application.operation_execution_manager import (
    OperationExecutionManager,
)
from app.application.operations import OperationCatalogService
from app.application.automation_schedule_manager import (
    AutomationScheduleManager,
)
from app.application.automation_schedule_targets import (
    AutomationScheduleCoordinator,
    PipelineScheduleTargetAdapter,
)
from app.application.automation_scheduling import (
    AutomationScheduleApplicationService,
)
from app.config.settings import PROJECT_ROOT, Settings
from app.infrastructure.database.migration import assert_schema_current
from app.services.notification_service import create_notification_service
from app.services.environment_configuration_service import (
    EnvironmentConfigurationService,
)
from app.utils.logger import configure_logging


def main() -> int:
    """Run the worker until the operating system requests shutdown."""
    initial_settings = Settings.from_env()
    logger = configure_logging(initial_settings.log_level).getChild("worker")
    assert_schema_current(
        initial_settings.database_target,
        project_root=PROJECT_ROOT,
    )
    settings = replace(
        EnvironmentConfigurationService(
            initial_settings,
            logger=logger.getChild("environment_configuration"),
        ).resolve_startup_settings(),
        execution_runtime="worker",
    )

    operation_manager = OperationExecutionManager(
        settings,
        logger=logger.getChild("operation_dispatch"),
    )
    operation_catalog = OperationCatalogService(
        settings,
        logger=logger.getChild("operation_catalog"),
    )
    automation_schedule_coordinator = AutomationScheduleCoordinator(
        AutomationScheduleApplicationService(settings.database_target),
        operation_manager,
        (
            PipelineScheduleTargetAdapter(
                settings,
                catalog=operation_catalog,
            ),
        ),
        notification_service=create_notification_service(
            settings.email_notifications,
            logger=logger.getChild("schedule_notifications"),
        ),
        environment_url=settings.epm_base_url,
        application_name=settings.application_name,
        logger=logger.getChild("automation_schedules"),
    )
    automation_schedule_manager = AutomationScheduleManager(
        automation_schedule_coordinator,
        poll_interval=settings.schedule_poll_interval,
        logger=logger.getChild("automation_schedule_manager"),
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

    automation_schedule_manager.start()
    try:
        worker.run_forever()
    finally:
        automation_schedule_manager.shutdown()
        operation_manager.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

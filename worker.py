"""Dedicated durable execution and schedule worker entry point."""

from __future__ import annotations

import logging
import signal
import threading
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
    build_schedule_target_adapters,
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
from app.services.application_workspace_service import (
    ApplicationWorkspaceService,
)
from app.products.registry import PRODUCT_PROVIDER_REGISTRY
from app.utils.logger import configure_logging


def main() -> int:
    """Run the worker until the operating system requests shutdown."""
    initial_settings = Settings.from_env()
    logger = configure_logging(initial_settings.log_level).getChild("worker")
    assert_schema_current(
        initial_settings.database_target,
        project_root=PROJECT_ROOT,
    )
    environment_configuration = EnvironmentConfigurationService(
        initial_settings,
        logger=logger.getChild("environment_configuration"),
    )
    resolved_settings = environment_configuration.resolve_startup_settings()
    application_workspaces = ApplicationWorkspaceService(
        resolved_settings.database_target
    )
    application_workspaces.synchronize(environment_configuration.get())
    deployment_workspace = application_workspaces.find_registered(
        resolved_settings.epm_base_url,
        resolved_settings.application_name,
    )
    business_process = environment_configuration.active_business_process(
        application_name=resolved_settings.application_name
    )
    product_provider = PRODUCT_PROVIDER_REGISTRY.get(business_process)
    if product_provider is None:
        logger.error(
            "Worker startup stopped: no enabled product provider is "
            "available for %s.",
            business_process.value,
        )
        return 1
    if not product_provider.operations():
        shutdown_event = threading.Event()

        def stop_read_only_worker(*_: object) -> None:
            logger.info("Read-only worker shutdown requested.")
            shutdown_event.set()

        signal.signal(signal.SIGINT, stop_read_only_worker)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, stop_read_only_worker)
        logger.info(
            "Worker is idle because %s currently exposes read-only "
            "capabilities and no executable operations.",
            business_process.value,
        )
        while not shutdown_event.wait(60):
            pass
        return 0
    settings = replace(resolved_settings, execution_runtime="worker")

    operation_manager = OperationExecutionManager(
        settings,
        logger=logger.getChild("operation_dispatch"),
    )
    operation_catalog = OperationCatalogService(
        settings,
        business_process=business_process,
        logger=logger.getChild("operation_catalog"),
    )
    automation_schedule_coordinator = AutomationScheduleCoordinator(
        AutomationScheduleApplicationService(settings.database_target),
        operation_manager,
        build_schedule_target_adapters(
            settings,
            catalog=operation_catalog,
        ),
        notification_service=create_notification_service(
            settings.email_notifications,
            logger=logger.getChild("schedule_notifications"),
        ),
        environment_url=settings.epm_base_url,
        application_name=settings.application_name,
        application_id=(
            deployment_workspace.application_id
            if deployment_workspace is not None
            else None
        ),
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

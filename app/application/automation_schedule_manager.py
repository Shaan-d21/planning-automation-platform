"""Background polling manager for generic automation schedules."""

from __future__ import annotations

import logging
from threading import Event, Thread

from app.application.automation_schedule_targets import (
    AutomationScheduleCoordinator,
)


class AutomationScheduleManager:
    """Poll due recurrences without coupling them to FastAPI."""

    def __init__(
        self,
        coordinator: AutomationScheduleCoordinator,
        *,
        poll_interval: float = 15.0,
        logger: logging.Logger | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._poll_interval = max(float(poll_interval), 1.0)
        self._logger = logger or logging.getLogger(__name__)
        self._stop = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(
            target=self._run,
            name="epm-automation-scheduler",
            daemon=True,
        )
        self._thread.start()
        self._logger.info("Automation schedule dispatcher started.")

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=min(self._poll_interval + 1, 5))
        self._logger.info("Automation schedule dispatcher stopped.")

    def tick(self) -> None:
        results = self._coordinator.dispatch_due()
        if results:
            self._logger.info(
                "Automation scheduler dispatched %s occurrence(s).",
                len(results),
            )

    def _run(self) -> None:
        while not self._stop.wait(self._poll_interval):
            try:
                self.tick()
            except Exception:
                self._logger.exception(
                    "Automation scheduler polling cycle failed."
                )

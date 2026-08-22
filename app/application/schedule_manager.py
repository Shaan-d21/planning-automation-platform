"""Background polling adapter for durable Process schedules."""

from __future__ import annotations

import logging
from threading import Event, Thread

from app.application.scheduling import ProcessScheduleApplicationService


class ProcessScheduleManager:
    """Poll due schedules without coupling recurrence to FastAPI."""

    def __init__(
        self,
        service: ProcessScheduleApplicationService,
        *,
        poll_interval: float = 15.0,
        logger: logging.Logger | None = None,
    ) -> None:
        self._service = service
        self._poll_interval = max(float(poll_interval), 1.0)
        self._logger = logger or logging.getLogger(__name__)
        self._stop = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        """Start one idempotent daemon polling thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(
            target=self._run,
            name="epm-process-scheduler",
            daemon=True,
        )
        self._thread.start()
        self._logger.info("Planning Process scheduler started.")

    def shutdown(self) -> None:
        """Stop polling without waiting for submitted Process executions."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=min(self._poll_interval + 1, 5))
        self._logger.info("Planning Process scheduler stopped.")

    def tick(self) -> None:
        """Run one scheduler polling cycle."""
        results = self._service.run_due()
        if results:
            self._logger.info(
                "Planning Process scheduler handled %s occurrence(s).",
                len(results),
            )

    def _run(self) -> None:
        while not self._stop.wait(self._poll_interval):
            try:
                self.tick()
            except Exception:
                self._logger.exception(
                    "Planning Process scheduler polling cycle failed."
                )

"""Centralized logging configuration."""

from __future__ import annotations

import logging
import sys


def configure_logging(level: str = "INFO") -> logging.Logger:
    """Configure and return the framework's root application logger.

    Repeated calls update the existing handler instead of adding duplicate
    handlers, which is useful for tests and embedded execution.
    """
    logger = logging.getLogger("oracle_planning_automation")
    logger.setLevel(level.upper())
    logger.propagate = False

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        setattr(handler, "_epm_console_handler", True)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
            )
        )
        logger.addHandler(handler)

    for handler in logger.handlers:
        if getattr(handler, "_epm_console_handler", False):
            handler.stream = sys.stdout
        handler.setLevel(level.upper())

    return logger

"""Centralized logging configuration."""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar, Token


_REQUEST_ID: ContextVar[str] = ContextVar(
    "oracle_epm_request_id",
    default="-",
)


class _RequestContextFilter(logging.Filter):
    """Attach the current HTTP correlation ID to every application record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _REQUEST_ID.get()
        return True


def bind_request_id(request_id: str) -> Token[str]:
    """Bind one HTTP correlation ID for the current async context."""
    return _REQUEST_ID.set(request_id)


def reset_request_id(token: Token[str]) -> None:
    """Restore the correlation context after an HTTP request completes."""
    _REQUEST_ID.reset(token)


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
                "%(asctime)s | %(levelname)s | %(name)s | "
                "request=%(request_id)s | %(message)s"
            )
        )
        handler.addFilter(_RequestContextFilter())
        logger.addHandler(handler)

    for handler in logger.handlers:
        if getattr(handler, "_epm_console_handler", False):
            handler.stream = sys.stdout
        handler.setLevel(level.upper())

    return logger

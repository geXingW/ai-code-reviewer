"""Logging configuration for the backend service."""

import json
import logging
import sys
from datetime import UTC, datetime
from logging import LogRecord

from app.core.config import Settings

RESERVED_LOG_RECORD_ATTRS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    """Format log records as compact JSON objects."""

    def format(self, record: LogRecord) -> str:
        """Render a log record as JSON.

        Args:
            record: Standard library log record.

        Returns:
            A JSON encoded log line.
        """

        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in RESERVED_LOG_RECORD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class ColorFormatter(logging.Formatter):
    """Format log records for local development."""

    COLORS = {
        logging.DEBUG: "\033[36m",
        logging.INFO: "\033[32m",
        logging.WARNING: "\033[33m",
        logging.ERROR: "\033[31m",
        logging.CRITICAL: "\033[35m",
    }
    RESET = "\033[0m"

    def format(self, record: LogRecord) -> str:
        """Render a colorized log line.

        Args:
            record: Standard library log record.

        Returns:
            A colorized plain text log line.
        """

        color = self.COLORS.get(record.levelno, "")
        message = super().format(record)
        return f"{color}{message}{self.RESET}" if color else message


def configure_logging(settings: Settings) -> None:
    """Configure root logging for the application.

    Args:
        settings: Application settings controlling log level and format.
    """

    handler = logging.StreamHandler(sys.stdout)
    if settings.debug:
        handler.setFormatter(ColorFormatter("%(levelname)s [%(name)s] %(message)s"))
    else:
        handler.setFormatter(JsonFormatter())

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(settings.log_level.upper())

    logging.getLogger("uvicorn.access").handlers.clear()

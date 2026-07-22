"""Standard-library logging setup shared by command-line entry points."""

import logging
from enum import StrEnum
from pathlib import Path


class LogLevel(StrEnum):
    """Supported application log levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


def configure_logging(level: LogLevel, log_file: Path | None = None) -> None:
    """Configure predictable process-wide console and optional file logging."""

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        expanded_log_file = log_file.expanduser().resolve(strict=False)
        expanded_log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(expanded_log_file, encoding="utf-8"))
    logging.basicConfig(
        level=level.value,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )

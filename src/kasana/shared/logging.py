"""Standard-library logging setup shared by command-line entry points."""

import logging
from enum import StrEnum


class LogLevel(StrEnum):
    """Supported application log levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


def configure_logging(level: LogLevel) -> None:
    """Configure a predictable process-wide console logger."""

    logging.basicConfig(
        level=level.value,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )

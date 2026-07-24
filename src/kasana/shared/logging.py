"""Standard-library logging setup shared by command-line entry points."""

import logging
import shutil
from enum import StrEnum
from pathlib import Path


class LogLevel(StrEnum):
    """Supported application log levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class LogDomain(StrEnum):
    """Kasana application domains that emit their own log file."""

    KASANA = "kasana"
    KATALOG = "katalog"
    KANVAS = "kanvas"
    KESTREL = "kestrel"
    KOURIER = "kourier"


def log_file_path(log_directory: Path, domain: LogDomain) -> Path:
    """Return the domain-specific log file within the configured log directory."""

    return log_directory / f"{domain.value}.log"


def configure_logging(level: LogLevel, domain: LogDomain, log_directory: Path) -> None:
    """Configure console logging and a fresh domain-specific log file.

    The previous session's log for this domain is retained in ``logs.old``
    (or the configured directory's ``.old`` sibling).
    """

    expanded_log_directory = log_directory.expanduser().resolve(strict=False)
    expanded_log_file = log_file_path(expanded_log_directory, domain)
    _close_root_handlers()
    expanded_log_directory.mkdir(parents=True, exist_ok=True)
    _archive_log_file(expanded_log_file)

    handlers: list[logging.Handler] = [
        logging.StreamHandler(),
        logging.FileHandler(expanded_log_file, encoding="utf-8"),
    ]
    logging.basicConfig(
        level=level.value,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def _close_root_handlers() -> None:
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        handler.close()


def _archive_log_file(log_file: Path) -> None:
    if not log_file.exists():
        return

    archive_directory = log_file.parent.with_name(f"{log_file.parent.name}.old")
    if archive_directory.is_symlink() or (
        archive_directory.exists() and not archive_directory.is_dir()
    ):
        _remove_path(archive_directory)
    archive_directory.mkdir(parents=True, exist_ok=True)
    archive_file = archive_directory / log_file.name
    _remove_path(archive_file)
    log_file.replace(archive_file)


def _remove_path(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
        return
    path.unlink()

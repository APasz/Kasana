"""Strict Kasana playback URI parsing and Linux XDG handler support."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

_MAX_URI_LENGTH = 256
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_DESKTOP_ENTRY_NAME = "kasana-kestrel.desktop"


class KestrelUriError(ValueError):
    """A Kasana URI is malformed or outside Kestrel's supported boundary."""


@dataclass(frozen=True)
class PlaybackUri:
    launch_token: str


def parse_playback_uri(uri: str) -> PlaybackUri:
    """Parse exactly ``kasana://play/<opaque-launch-token>``."""

    if not uri or len(uri) > _MAX_URI_LENGTH:
        raise KestrelUriError("Kasana playback URI length is invalid.")
    try:
        parsed = urlsplit(uri)
        port = parsed.port
    except ValueError as error:
        raise KestrelUriError("Kasana URI must use kasana://play/<launch-token>.") from error
    if (
        parsed.scheme != "kasana"
        or parsed.netloc != "play"
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
    ):
        raise KestrelUriError("Kasana URI must use kasana://play/<launch-token>.")
    path_parts = parsed.path.split("/")
    if len(path_parts) != 2 or path_parts[0] or not _TOKEN_PATTERN.fullmatch(path_parts[1]):
        raise KestrelUriError("Kasana URI launch token is invalid.")
    return PlaybackUri(launch_token=path_parts[1])


def validate_launch_token(launch_token: str) -> str:
    """Validate a token supplied by the direct CLI command."""

    if not _TOKEN_PATTERN.fullmatch(launch_token):
        raise KestrelUriError("Launch token is invalid.")
    return launch_token


def default_xdg_data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))


def desktop_entry_path(data_home: Path | None = None) -> Path:
    return (data_home or default_xdg_data_home()) / "applications" / _DESKTOP_ENTRY_NAME


def install_uri_handler(
    *,
    executable: Path,
    data_home: Path | None = None,
    xdg_mime_executable: str | None = None,
) -> Path:
    """Install an XDG ``kasana`` scheme handler without elevated privileges."""

    resolved_executable = executable.expanduser().resolve(strict=False)
    if not resolved_executable.is_absolute():
        raise ValueError("Kestrel URI-handler executable must be absolute.")
    target = desktop_entry_path(data_home)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _write_private_file(target, _desktop_entry(resolved_executable), mode=0o644)
    _run_xdg_mime(("default", _DESKTOP_ENTRY_NAME, "x-scheme-handler/kasana"), xdg_mime_executable)
    return target


def uninstall_uri_handler(
    *, data_home: Path | None = None, xdg_mime_executable: str | None = None
) -> Path:
    """Remove Kestrel's own XDG handler desktop entry if it is installed."""

    target = desktop_entry_path(data_home)
    try:
        target.unlink()
    except FileNotFoundError:
        pass
    return target


def uri_handler_is_registered(
    *, data_home: Path | None = None, xdg_mime_executable: str | None = None
) -> bool:
    target = desktop_entry_path(data_home)
    if not target.is_file():
        return False
    executable = _find_xdg_mime(xdg_mime_executable)
    if executable is None:
        return True
    try:
        completed = subprocess.run(
            [executable, "query", "default", "x-scheme-handler/kasana"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except OSError:
        return False
    return completed.returncode == 0 and completed.stdout.strip() == _DESKTOP_ENTRY_NAME


def console_executable() -> Path:
    """Return the current command entry point for the generated desktop entry."""

    return Path(sys.argv[0]).expanduser().resolve(strict=False)


def _desktop_entry(executable: Path) -> str:
    escaped_executable = _desktop_exec_argument(str(executable))
    return "\n".join(
        (
            "[Desktop Entry]",
            "Type=Application",
            "Name=Kasana Kestrel",
            "Comment=Play Kasana media",
            f"Exec={escaped_executable} handle-uri %u",
            "NoDisplay=true",
            "Terminal=false",
            "MimeType=x-scheme-handler/kasana;",
            "Categories=AudioVideo;Player;",
            "",
        )
    )


def _desktop_exec_argument(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("`", "\\`").replace("$", "\\$")
    return f'"{escaped}"'


def _write_private_file(target: Path, content: str, *, mode: int) -> None:
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", dir=target.parent, text=True
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(file_descriptor, mode)
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as file_handle:
            file_handle.write(content)
        os.replace(temporary_path, target)
        target.chmod(mode)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _run_xdg_mime(
    arguments: tuple[str, ...], executable: str | None, *, required: bool = True
) -> None:
    command = _find_xdg_mime(executable)
    if command is None:
        if required:
            raise RuntimeError("xdg-mime is required to register the Kasana URI handler.")
        return
    try:
        completed = subprocess.run(
            [command, *arguments], check=False, capture_output=True, text=True, timeout=10
        )
    except OSError as error:
        if required:
            raise RuntimeError("xdg-mime could not register the Kasana URI handler.") from error
        return
    if completed.returncode != 0 and required:
        raise RuntimeError("xdg-mime could not register the Kasana URI handler.")


def _find_xdg_mime(configured: str | None) -> str | None:
    if configured is not None:
        return configured
    return shutil.which("xdg-mime")

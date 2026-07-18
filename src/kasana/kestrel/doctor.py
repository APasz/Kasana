"""Health checks for Kestrel's local playback prerequisites."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from kasana.kestrel.mpv import check_ipc_capability, discover_mpv, mpv_version
from kasana.kestrel.settings import KestrelSettings
from kasana.kestrel.uri import uri_handler_is_registered


class HealthCatalogClient(Protocol):
    async def health(self) -> object: ...

    async def close(self) -> None: ...


@dataclass(frozen=True)
class DoctorReport:
    katalog_connected: bool
    mpv_path: Path | None
    mpv_version: str | None
    runtime_directory_writable: bool
    temporary_directory_writable: bool
    uri_handler_registered: bool
    ipc_capable: bool

    @property
    def healthy(self) -> bool:
        return (
            self.katalog_connected
            and self.mpv_path is not None
            and self.mpv_version is not None
            and self.runtime_directory_writable
            and self.temporary_directory_writable
            and self.uri_handler_registered
            and self.ipc_capable
        )


async def run_doctor(settings: KestrelSettings, catalog: HealthCatalogClient) -> DoctorReport:
    """Check Katalog, mpv, writable private directories, XDG, and Unix IPC."""

    katalog_connected = await _catalog_is_reachable(catalog)
    executable = discover_mpv(settings.mpv_executable)
    version = await _mpv_version(executable)
    runtime_directory = settings.runtime_directory.expanduser().resolve(strict=False)
    temporary_directory = settings.temporary_directory.expanduser().resolve(strict=False)
    runtime_writable = _directory_is_writable(runtime_directory)
    temporary_writable = _directory_is_writable(temporary_directory)
    ipc_capable = await check_ipc_capability(runtime_directory) if runtime_writable else False
    return DoctorReport(
        katalog_connected=katalog_connected,
        mpv_path=executable,
        mpv_version=version,
        runtime_directory_writable=runtime_writable,
        temporary_directory_writable=temporary_writable,
        uri_handler_registered=uri_handler_is_registered(),
        ipc_capable=ipc_capable,
    )


async def _catalog_is_reachable(catalog: HealthCatalogClient) -> bool:
    try:
        await catalog.health()
    except Exception:
        return False
    return True


async def _mpv_version(executable: Path | None) -> str | None:
    if executable is None:
        return None
    try:
        return await mpv_version(executable)
    except OSError:
        return None


def _directory_is_writable(directory: Path) -> bool:
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        file_descriptor, temporary_name = tempfile.mkstemp(prefix=".kestrel-doctor-", dir=directory)
        try:
            os.close(file_descriptor)
        finally:
            Path(temporary_name).unlink(missing_ok=True)
    except OSError:
        return False
    return True

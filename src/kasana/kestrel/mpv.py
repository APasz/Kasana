"""mpv discovery and its private JSON IPC protocol."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

from kasana.shared.concurrency import run_blocking

_MAX_IPC_MESSAGE_BYTES = 64 * 1024


class MpvError(RuntimeError):
    """Base error for mpv discovery, launch, or IPC failures."""


class MpvIpcUnavailableError(MpvError):
    """mpv did not create or retain its requested IPC endpoint."""


def discover_mpv(executable: str) -> Path | None:
    """Resolve a configured mpv executable without invoking a shell."""

    configured = Path(executable).expanduser()
    if "/" in executable:
        candidate = configured.resolve(strict=False)
        return candidate if candidate.is_file() and os.access(candidate, os.X_OK) else None
    resolved = shutil.which(executable)
    return Path(resolved).resolve() if resolved is not None else None


async def mpv_version(executable: Path, *, timeout_seconds: float = 5.0) -> str | None:
    """Return mpv's first version line, bounded to avoid a hanging doctor command."""

    process = await asyncio.create_subprocess_exec(
        str(executable),
        "--version",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout_seconds)
    except TimeoutError:
        process.kill()
        await process.wait()
        return None
    if process.returncode != 0:
        return None
    first_line = stdout.decode("utf-8", errors="replace").splitlines()
    return first_line[0][:200] if first_line else None


class MpvIpcClient:
    """One private JSON line connection to mpv's Unix-domain IPC server."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer
        self._write_lock = asyncio.Lock()
        self._closed = False

    @classmethod
    async def connect(cls, socket_path: Path, *, timeout_seconds: float) -> MpvIpcClient:
        deadline = time.monotonic() + timeout_seconds
        last_error: OSError | None = None
        while time.monotonic() < deadline:
            try:
                reader, writer = await asyncio.open_unix_connection(str(socket_path))
            except OSError as error:
                last_error = error
                await asyncio.sleep(0.05)
                continue
            try:
                await run_blocking(_secure_socket, socket_path)
            except OSError:
                await _close_writer(writer)
                raise MpvIpcUnavailableError(
                    "mpv IPC socket permissions could not be secured."
                ) from None
            return cls(reader, writer)
        raise MpvIpcUnavailableError("mpv IPC socket was unavailable.") from last_error

    async def observe_properties(self, properties: tuple[str, ...]) -> None:
        for request_id, property_name in enumerate(properties, start=1):
            await self.command(("observe_property", request_id, property_name))

    async def command(self, command: tuple[object, ...]) -> None:
        if self._closed:
            raise MpvIpcUnavailableError("mpv IPC connection is closed.")
        payload = json.dumps({"command": command}, separators=(",", ":")).encode() + b"\n"
        async with self._write_lock:
            self._writer.write(payload)
            try:
                await self._writer.drain()
            except (ConnectionError, OSError) as error:
                raise MpvIpcUnavailableError("mpv IPC command could not be sent.") from error

    async def next_message(self) -> dict[str, object]:
        try:
            line = await self._reader.readline()
        except (ConnectionError, OSError) as error:
            raise MpvIpcUnavailableError("mpv IPC connection was interrupted.") from error
        if not line:
            raise MpvIpcUnavailableError("mpv IPC connection closed unexpectedly.")
        if len(line) > _MAX_IPC_MESSAGE_BYTES:
            raise MpvIpcUnavailableError("mpv IPC sent an oversized message.")
        try:
            payload = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise MpvIpcUnavailableError("mpv IPC sent malformed JSON.") from error
        if not isinstance(payload, dict):
            raise MpvIpcUnavailableError("mpv IPC sent an invalid message.")
        return cast(dict[str, object], payload)

    async def messages(self) -> AsyncIterator[dict[str, object]]:
        while True:
            yield await self.next_message()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await _close_writer(self._writer)


async def check_ipc_capability(runtime_directory: Path) -> bool:
    """Verify that this process can create a private Unix-domain socket."""

    probe_path = runtime_directory / ".ipc-probe.sock"
    server: asyncio.AbstractServer | None = None
    try:
        await run_blocking(_prepare_runtime_directory, runtime_directory)
        await run_blocking(_unlink_if_present, probe_path)
        server = await asyncio.start_unix_server(_accept_ipc_probe, path=str(probe_path))
        await run_blocking(_secure_socket, probe_path)
        return True
    except OSError:
        return False
    finally:
        if server is not None:
            server.close()
            await server.wait_closed()
        try:
            await run_blocking(_unlink_if_present, probe_path)
        except OSError:
            pass


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    writer.close()
    try:
        await writer.wait_closed()
    except ConnectionError, OSError:
        pass


def _prepare_runtime_directory(directory: Path) -> None:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)


def _secure_socket(socket_path: Path) -> None:
    socket_path.chmod(0o600)


def _unlink_if_present(path: Path) -> None:
    path.unlink(missing_ok=True)


def _accept_ipc_probe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    del reader
    writer.close()

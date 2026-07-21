"""mpv-first Kestrel playback orchestration against Katalog's public API."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from urllib.parse import urljoin

from kasana.katalog.public import (
    PlaybackSessionResponse,
    SessionProgressUpdate,
)
from kasana.kestrel.mpv import MpvError, MpvIpcClient, MpvIpcUnavailableError, discover_mpv
from kasana.kestrel.settings import KestrelSettings, PlayerBackend
from kasana.kestrel.uri import validate_launch_token

_OBSERVED_PROPERTIES = (
    "time-pos",
    "duration",
    "pause",
    "seeking",
    "playlist-pos",
    "path",
)
_MEDIA_STREAM_PATH_PATTERN = re.compile(r"^/api/v1/media/[A-Za-z0-9_-]+$")


class PlaybackCatalogueClient(Protocol):
    async def launch_playback_plan(self, launch_token: str) -> PlaybackSessionResponse: ...

    async def update_playback_session_progress(
        self, session_id: str, update: SessionProgressUpdate
    ) -> object: ...

    async def advance_playback_session(self, session_id: str) -> PlaybackSessionResponse: ...

    async def complete_playback_session(self, session_id: str) -> object: ...

    async def close_playback_session(self, session_id: str) -> None: ...


class MpvProcess(Protocol):
    @property
    def returncode(self) -> int | None: ...

    async def wait(self) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


class PlaybackOutcome(StrEnum):
    COMPLETED = "completed"
    STOPPED = "stopped"
    CRASHED = "crashed"


class KestrelPlaybackError(RuntimeError):
    """Kestrel could not safely complete an mpv playback session."""


@dataclass(frozen=True)
class PlaybackResult:
    session_id: str
    outcome: PlaybackOutcome
    returncode: int | None


@dataclass(frozen=True)
class _SessionFiles:
    directory: Path
    playlist_path: Path
    socket_path: Path


@dataclass
class _MpvPlaybackState:
    session: PlaybackSessionResponse
    playlist_position: int = 0
    position_seconds: float = 0.0
    duration_seconds: float | None = None
    paused: bool = False
    seeking: bool = False
    completed_positions: set[int] = field(default_factory=set)
    last_reported_position: float | None = None
    last_forced_reason: str | None = None
    saw_error: bool = False


class MpvPlayerAgent:
    """Runs one Katalog playback session in one private mpv process."""

    def __init__(
        self,
        settings: KestrelSettings,
        catalogue: PlaybackCatalogueClient,
    ) -> None:
        if settings.player_backend is not PlayerBackend.MPV:
            raise KestrelPlaybackError("Only the mpv backend is implemented.")
        self._settings = settings
        self._catalogue = catalogue
        self._runtime_directory = settings.runtime_directory.expanduser().resolve(strict=False)

    async def play(self, launch_token: str) -> PlaybackResult:
        """Exchange a launch token, run mpv, report state, and remove all local secrets."""

        validate_launch_token(launch_token)
        try:
            session = await self._catalogue.launch_playback_plan(launch_token)
        except Exception as error:
            raise KestrelPlaybackError("Katalog could not launch the playback session.") from error
        if session.current_item is None:
            raise KestrelPlaybackError("Katalog session has no current playback item.")
        state = _MpvPlaybackState(
            session=session,
            position_seconds=session.current_item.saved_resume_position_seconds,
            duration_seconds=session.current_item.duration_seconds,
        )
        session_files: _SessionFiles | None = None
        process: MpvProcess | None = None
        ipc: MpvIpcClient | None = None
        try:
            executable = discover_mpv(self._settings.mpv_executable)
            if executable is None:
                raise KestrelPlaybackError("mpv executable is unavailable.")
            try:
                session_files = self._create_session_files()
                self._write_playlist(session_files.playlist_path, session)
            except OSError as error:
                raise KestrelPlaybackError(
                    "Kestrel could not create private session files."
                ) from error
            try:
                process = await self._launch_mpv(executable, session_files, state.position_seconds)
            except OSError as error:
                raise KestrelPlaybackError("mpv could not be started.") from error
            ipc = await MpvIpcClient.connect(
                session_files.socket_path,
                timeout_seconds=self._settings.ipc_connect_timeout_seconds,
            )
            await ipc.observe_properties(_OBSERVED_PROPERTIES)
            return await self._monitor(process, ipc, state, session_files.socket_path)
        except asyncio.CancelledError:
            await self._report_progress(state, force=True, reason="shutdown")
            raise
        except MpvError as error:
            raise KestrelPlaybackError("mpv IPC is unavailable.") from error
        finally:
            if ipc is not None:
                await ipc.close()
            if process is not None:
                await _stop_process(process)
            await self._close_catalogue_session(session.id)
            if session_files is not None:
                self._remove_session_files(session_files)

    async def _monitor(
        self,
        process: MpvProcess,
        ipc: MpvIpcClient,
        state: _MpvPlaybackState,
        socket_path: Path,
    ) -> PlaybackResult:
        process_task = asyncio.create_task(process.wait())
        message_task: asyncio.Task[dict[str, object]] | None = None
        ipc_reconnects_remaining = 1
        try:
            while True:
                message_task = asyncio.create_task(ipc.next_message())
                done, _ = await asyncio.wait(
                    (process_task, message_task),
                    timeout=self._settings.progress_interval_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    message_task.cancel()
                    await _await_cancelled(message_task)
                    await self._report_progress(state, force=False, reason="periodic")
                    continue
                if message_task in done:
                    try:
                        message = message_task.result()
                    except MpvIpcUnavailableError:
                        if process_task not in done:
                            if ipc_reconnects_remaining == 0 or process.returncode is not None:
                                raise
                            ipc_reconnects_remaining -= 1
                            await ipc.close()
                            ipc = await MpvIpcClient.connect(
                                socket_path,
                                timeout_seconds=self._settings.ipc_connect_timeout_seconds,
                            )
                            await ipc.observe_properties(_OBSERVED_PROPERTIES)
                            continue
                    else:
                        result = await self._handle_mpv_message(message, state)
                        if result is PlaybackOutcome.COMPLETED:
                            await self._report_progress(state, force=True, reason="shutdown")
                            returncode = (
                                process_task.result()
                                if process_task.done()
                                else await _wait_for_exit(process)
                            )
                            return PlaybackResult(
                                session_id=state.session.id,
                                outcome=PlaybackOutcome.COMPLETED,
                                returncode=returncode,
                            )
                if process_task in done:
                    if not message_task.done():
                        message_task.cancel()
                        await _await_cancelled(message_task)
                    if await self._drain_after_process_exit(ipc, state):
                        await self._report_progress(state, force=True, reason="shutdown")
                        return PlaybackResult(
                            session_id=state.session.id,
                            outcome=PlaybackOutcome.COMPLETED,
                            returncode=process_task.result(),
                        )
                    returncode = process_task.result()
                    await self._report_progress(state, force=True, reason="shutdown")
                    outcome = (
                        PlaybackOutcome.CRASHED if returncode != 0 else PlaybackOutcome.STOPPED
                    )
                    return PlaybackResult(
                        session_id=state.session.id,
                        outcome=outcome,
                        returncode=returncode,
                    )
        finally:
            if message_task is not None:
                if not message_task.done():
                    message_task.cancel()
                    await _await_cancelled(message_task)
                else:
                    try:
                        message_task.result()
                    except asyncio.CancelledError, MpvIpcUnavailableError:
                        pass
            if not process_task.done():
                process_task.cancel()
                await _await_cancelled(process_task)

    async def _drain_after_process_exit(self, ipc: MpvIpcClient, state: _MpvPlaybackState) -> bool:
        """Apply IPC events delivered immediately before mpv's process exit."""

        while True:
            try:
                message = await asyncio.wait_for(ipc.next_message(), timeout=0.05)
            except TimeoutError, MpvIpcUnavailableError:
                return self._all_entries_completed(state)
            result = await self._handle_mpv_message(message, state)
            if result is PlaybackOutcome.COMPLETED:
                return True

    async def _handle_mpv_message(
        self, message: dict[str, object], state: _MpvPlaybackState
    ) -> PlaybackOutcome | None:
        event = message.get("event")
        if event == "property-change":
            await self._handle_property_change(message, state)
            return None
        if event == "file-loaded":
            await self._report_progress(state, force=True, reason="file-loaded")
            return None
        if event == "end-file":
            reason = message.get("reason")
            if reason == "error":
                state.saw_error = True
                return None
            if reason == "eof" and state.duration_seconds is not None:
                state.position_seconds = state.duration_seconds
            if not state.saw_error and (reason == "eof" or self._is_complete(state)):
                await self._report_progress(state, force=True, reason="end-file")
                await self._complete_current_entry(state)
            else:
                await self._report_progress(state, force=True, reason="end-file")
            return None
        if event == "shutdown":
            return PlaybackOutcome.COMPLETED if self._all_entries_completed(state) else None
        return None

    async def _handle_property_change(
        self, message: dict[str, object], state: _MpvPlaybackState
    ) -> None:
        property_name = message.get("name")
        value = message.get("data")
        if property_name == "time-pos":
            position = _optional_number(value)
            if position is not None:
                state.position_seconds = max(0.0, position)
            return
        if property_name == "duration":
            duration = _optional_number(value)
            state.duration_seconds = max(0.0, duration) if duration is not None else None
            return
        if property_name == "pause" and isinstance(value, bool):
            changed = state.paused != value
            state.paused = value
            if changed:
                await self._report_progress(state, force=True, reason="pause")
            return
        if property_name == "seeking" and isinstance(value, bool):
            was_seeking = state.seeking
            state.seeking = value
            if was_seeking and not value:
                await self._report_progress(state, force=True, reason="seek", seek=True)
            return
        if property_name == "playlist-pos":
            playlist_position = _optional_nonnegative_int(value)
            if playlist_position is not None:
                await self._advance_to_playlist_position(playlist_position, state)

    async def _advance_to_playlist_position(
        self, playlist_position: int, state: _MpvPlaybackState
    ) -> None:
        if playlist_position == state.playlist_position:
            return
        if playlist_position < state.playlist_position:
            raise KestrelPlaybackError("mpv moved backwards in the playback queue.")
        await self._report_progress(state, force=True, reason="transition")
        while state.playlist_position < playlist_position:
            state.session = await self._catalogue.advance_playback_session(state.session.id)
            state.playlist_position += 1
        current_item = state.session.current_item
        if current_item is None:
            raise KestrelPlaybackError("Katalog session has no current playback item.")
        state.position_seconds = current_item.saved_resume_position_seconds
        state.duration_seconds = current_item.duration_seconds
        state.paused = False
        state.seeking = False
        state.last_reported_position = None
        state.last_forced_reason = None
        state.saw_error = False

    async def _report_progress(
        self,
        state: _MpvPlaybackState,
        *,
        force: bool,
        reason: str,
        seek: bool = False,
    ) -> None:
        if state.playlist_position in state.completed_positions:
            return
        if state.seeking and not seek:
            return
        position = state.position_seconds
        if not force and (state.paused or state.last_reported_position is None):
            if state.paused:
                return
        unchanged = (
            state.last_reported_position is not None
            and abs(position - state.last_reported_position)
            < self._settings.progress_position_delta_seconds
        )
        if not force and unchanged:
            return
        if force and unchanged and state.last_forced_reason == reason and not seek:
            return
        await self._catalogue.update_playback_session_progress(
            state.session.id,
            SessionProgressUpdate(position_seconds=position, seek=seek),
        )
        state.last_reported_position = position
        state.last_forced_reason = reason if force else None

    async def _complete_current_entry(self, state: _MpvPlaybackState) -> None:
        if state.playlist_position in state.completed_positions:
            return
        await self._catalogue.complete_playback_session(state.session.id)
        state.completed_positions.add(state.playlist_position)

    def _is_complete(self, state: _MpvPlaybackState) -> bool:
        if state.duration_seconds is None or state.duration_seconds <= 0:
            return False
        completion_fraction = state.position_seconds / state.duration_seconds
        return completion_fraction >= self._settings.completion_threshold

    def _all_entries_completed(self, state: _MpvPlaybackState) -> bool:
        return len(state.completed_positions) == len(state.session.entries)

    async def _launch_mpv(
        self, executable: Path, session_files: _SessionFiles, resume_position: float
    ) -> MpvProcess:
        return await asyncio.create_subprocess_exec(
            str(executable),
            "--no-terminal",
            "--force-window=yes",
            f"--input-ipc-server={session_files.socket_path}",
            f"--playlist={session_files.playlist_path}",
            "--playlist-start=0",
            f"--start={resume_position:.3f}",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

    def _create_session_files(self) -> _SessionFiles:
        self._runtime_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._runtime_directory.chmod(0o700)
        directory = Path(tempfile.mkdtemp(prefix="session-", dir=self._runtime_directory))
        directory.chmod(0o700)
        socket_path = directory / "mpv.sock"
        if len(os.fsencode(socket_path)) > 100:
            shutil.rmtree(directory)
            raise KestrelPlaybackError("Kestrel runtime path is too long for mpv IPC.")
        return _SessionFiles(
            directory=directory,
            playlist_path=directory / "queue.m3u",
            socket_path=socket_path,
        )

    def _write_playlist(self, path: Path, session: PlaybackSessionResponse) -> None:
        lines = ["#EXTM3U"]
        for entry in session.entries:
            if not _MEDIA_STREAM_PATH_PATTERN.fullmatch(entry.stream_url):
                raise KestrelPlaybackError("Katalog returned an invalid stream URL.")
            lines.append(urljoin(f"{self._settings.katalog_url.rstrip('/')}/", entry.stream_url))
        file_descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as playlist:
            playlist.write("\n".join(lines))
            playlist.write("\n")

    async def _close_catalogue_session(self, session_id: str) -> None:
        try:
            await self._catalogue.close_playback_session(session_id)
        except Exception:
            return

    def _remove_session_files(self, session_files: _SessionFiles) -> None:
        if self._runtime_directory not in session_files.directory.parents:
            raise RuntimeError("Refusing to remove an invalid Kestrel session directory.")
        shutil.rmtree(session_files.directory, ignore_errors=True)


async def _stop_process(process: MpvProcess) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=3)
    except TimeoutError:
        process.kill()
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except TimeoutError:
            return


async def _wait_for_exit(process: MpvProcess) -> int | None:
    if process.returncode is not None:
        return process.returncode
    try:
        return await asyncio.wait_for(process.wait(), timeout=3)
    except TimeoutError:
        return None


async def _await_cancelled(task: asyncio.Task[object]) -> None:
    try:
        await task
    except asyncio.CancelledError:
        return


def _optional_number(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _optional_nonnegative_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None

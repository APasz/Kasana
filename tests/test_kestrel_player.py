"""Fake-backed tests for Kestrel's mpv-native playback workflow."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from _pytest.monkeypatch import MonkeyPatch

from kasana.katalog.public import (
    PlaybackContext,
    PlaybackContextKind,
    PlaybackPlanEntry,
    PlaybackSessionResponse,
    SessionProgressUpdate,
)
from kasana.kestrel.player import (
    KestrelPlaybackError,
    MpvPlayerAgent,
    PlaybackOutcome,
)
from kasana.kestrel.settings import KestrelSettings
from kasana.kestrel.uri import KestrelUriError
from kasana.shared.concurrency import run_blocking

_TOKEN = "a" * 43


@dataclass
class FakeKatalogClient:
    session: PlaybackSessionResponse
    launched_tokens: list[str] = field(default_factory=list)
    progress_updates: list[tuple[int, SessionProgressUpdate]] = field(default_factory=list)
    completed_positions: list[int] = field(default_factory=list)
    advance_count: int = 0
    closed_session_ids: list[str] = field(default_factory=list)

    async def launch_playback_plan(self, launch_token: str) -> PlaybackSessionResponse:
        self.launched_tokens.append(launch_token)
        return self.session

    async def update_playback_session_progress(
        self, session_id: str, update: SessionProgressUpdate
    ) -> object:
        assert session_id == self.session.id
        self.progress_updates.append((self.session.current_entry_position, update))
        return object()

    async def advance_playback_session(self, session_id: str) -> PlaybackSessionResponse:
        assert session_id == self.session.id
        self.advance_count += 1
        next_position = self.session.current_entry_position + 1
        assert next_position < len(self.session.entries)
        self.session = self.session.model_copy(
            update={
                "current_entry_position": next_position,
                "current_item": self.session.entries[next_position],
            }
        )
        return self.session

    async def complete_playback_session(self, session_id: str) -> object:
        assert session_id == self.session.id
        self.completed_positions.append(self.session.current_entry_position)
        return object()

    async def close_playback_session(self, session_id: str) -> None:
        assert session_id == self.session.id
        self.closed_session_ids.append(session_id)


class FakeMpvProcess:
    def __init__(self) -> None:
        self._returncode: int | None = None
        self._waiter: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        self.terminated = False
        self.killed = False

    @property
    def returncode(self) -> int | None:
        return self._returncode

    async def wait(self) -> int:
        return await asyncio.shield(self._waiter)

    def finish(self, returncode: int) -> None:
        if self._returncode is not None:
            return
        self._returncode = returncode
        if not self._waiter.done():
            self._waiter.set_result(returncode)

    def terminate(self) -> None:
        self.terminated = True
        self.finish(0)

    def kill(self) -> None:
        self.killed = True
        self.finish(-9)


class FakeMpvIpcServer:
    """A minimal Unix JSON IPC peer that records mpv launch state."""

    def __init__(self) -> None:
        self.process = FakeMpvProcess()
        self.arguments: tuple[str, ...] = ()
        self.playlist_contents = ""
        self.playlist_mode = 0
        self.socket_path: Path | None = None
        self.observed_properties: list[str] = []
        self._server: asyncio.AbstractServer | None = None
        self._writer: asyncio.StreamWriter | None = None
        self.connected = asyncio.Event()

    async def launch(self, *arguments: str, **_kwargs: object) -> FakeMpvProcess:
        self.arguments = arguments
        socket_argument = next(
            argument for argument in arguments if argument.startswith("--input-ipc-server=")
        )
        playlist_argument = next(
            argument for argument in arguments if argument.startswith("--playlist=")
        )
        self.socket_path = Path(socket_argument.removeprefix("--input-ipc-server="))
        playlist_path = Path(playlist_argument.removeprefix("--playlist="))
        self.playlist_contents = await run_blocking(playlist_path.read_text, encoding="utf-8")
        self.playlist_mode = os.stat(playlist_path).st_mode & 0o777
        self._server = await asyncio.start_unix_server(
            self._handle_connection, str(self.socket_path)
        )
        return self.process

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._writer = writer
        self.connected.set()
        try:
            while line := await reader.readline():
                payload = cast(object, json.loads(line))
                if not isinstance(payload, dict):
                    continue
                command = cast(dict[str, object], payload).get("command")
                if not isinstance(command, list):
                    continue
                values = cast(list[object], command)
                if (
                    len(values) == 3
                    and values[0] == "observe_property"
                    and isinstance(values[2], str)
                ):
                    self.observed_properties.append(values[2])
        finally:
            writer.close()
            await writer.wait_closed()

    async def send(self, message: dict[str, object]) -> None:
        await asyncio.wait_for(self.connected.wait(), timeout=2)
        assert self._writer is not None
        self._writer.write(json.dumps(message).encode("utf-8") + b"\n")
        await self._writer.drain()

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        if self._writer is not None:
            self._writer.close()
            await self._writer.wait_closed()


@dataclass
class RejectingKatalogClient(FakeKatalogClient):
    async def launch_playback_plan(self, launch_token: str) -> PlaybackSessionResponse:
        del launch_token
        raise RuntimeError("access token leaked")


def _entry(position: int, *, resume: float = 0.0, title: str | None = None) -> PlaybackPlanEntry:
    token = f"{position + 1}" * 43
    return PlaybackPlanEntry(
        position=position,
        item_id=position + 1,
        display_title=title or f"Item {position + 1}",
        duration_seconds=100.0,
        saved_resume_position_seconds=resume,
        stream_url=f"/api/v1/media/{token}",
        download_url=f"/api/v1/downloads/{token}",
    )


def _session(
    entries: tuple[PlaybackPlanEntry, ...], *, context: PlaybackContext | None = None
) -> PlaybackSessionResponse:
    now = datetime.now(UTC)
    return PlaybackSessionResponse(
        id="s" * 43,
        user_id=1,
        context=context or PlaybackContext(kind=PlaybackContextKind.STANDALONE, item_id=1),
        current_entry_position=0,
        current_item=entries[0],
        entries=entries,
        created_at=now,
        expires_at=now + timedelta(minutes=10),
        closed_at=None,
    )


def _settings(
    tmp_path: Path,
    *,
    ipc_connect_timeout_seconds: float = 0.2,
    progress_interval_seconds: float = 0.05,
    progress_position_delta_seconds: float = 0.1,
) -> KestrelSettings:
    return KestrelSettings(
        katalog_url="http://katalog.test",
        runtime_directory=tmp_path / "runtime",
        temporary_directory=tmp_path / "temporary",
        ipc_connect_timeout_seconds=ipc_connect_timeout_seconds,
        progress_interval_seconds=progress_interval_seconds,
        progress_position_delta_seconds=progress_position_delta_seconds,
    )


def _patch_mpv(monkeypatch: MonkeyPatch, server: FakeMpvIpcServer) -> None:
    def fake_discover(_executable: str) -> Path:
        return Path("/fake/mpv")

    monkeypatch.setattr("kasana.kestrel.player.discover_mpv", fake_discover)
    monkeypatch.setattr(
        "kasana.kestrel.player.asyncio.create_subprocess_exec",
        cast(Callable[..., object], server.launch),
    )


async def _await_connection(server: FakeMpvIpcServer) -> None:
    await asyncio.wait_for(server.connected.wait(), timeout=2)


async def _wait_until(condition: Callable[[], bool]) -> None:
    deadline = asyncio.get_running_loop().time() + 2
    while not condition():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("Timed out waiting for fake mpv state.")
        await asyncio.sleep(0.01)


async def _close_playback_task(task: asyncio.Task[object], server: FakeMpvIpcServer) -> None:
    if not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task
    await server.close()


async def _finish_single_item(server: FakeMpvIpcServer) -> None:
    await server.send({"event": "property-change", "name": "duration", "data": 100.0})
    await server.send({"event": "property-change", "name": "time-pos", "data": 100.0})
    await server.send({"event": "end-file", "reason": "eof"})
    await server.send({"event": "shutdown"})
    server.process.finish(0)


async def test_player_launches_private_playlist_and_resumes(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    catalog = FakeKatalogClient(_session((_entry(0, resume=12.5),)))
    server = FakeMpvIpcServer()
    _patch_mpv(monkeypatch, server)
    agent = MpvPlayerAgent(_settings(tmp_path), catalog)

    task = asyncio.create_task(agent.play(_TOKEN))
    try:
        await _await_connection(server)
        assert "--start=12.500" in server.arguments
        assert server.playlist_contents == (
            "#EXTM3U\nhttp://katalog.test/api/v1/media/" + "1" * 43 + "\n"
        )
        assert server.playlist_mode == 0o600
        await _wait_until(
            lambda: (
                server.socket_path is not None
                and os.stat(server.socket_path).st_mode & 0o777 == 0o600
            )
        )
        await _finish_single_item(server)
        result = await task
        await _wait_until(lambda: len(server.observed_properties) == 6)
    finally:
        await _close_playback_task(task, server)

    assert result.outcome is PlaybackOutcome.COMPLETED
    assert catalog.launched_tokens == [_TOKEN]
    assert catalog.completed_positions == [0]
    assert catalog.closed_session_ids == [catalog.session.id]
    assert list((tmp_path / "runtime").iterdir()) == []
    assert set(server.observed_properties) == {
        "time-pos",
        "duration",
        "pause",
        "seeking",
        "playlist-pos",
        "path",
    }


async def test_player_advances_a_mixed_watch_order_queue(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    context = PlaybackContext(kind=PlaybackContextKind.WATCH_ORDER, watch_order_id=8)
    catalog = FakeKatalogClient(
        _session((_entry(0, title="Film"), _entry(1, title="Episode")), context=context)
    )
    server = FakeMpvIpcServer()
    _patch_mpv(monkeypatch, server)
    task = asyncio.create_task(MpvPlayerAgent(_settings(tmp_path), catalog).play(_TOKEN))
    try:
        await _await_connection(server)
        await server.send({"event": "property-change", "name": "time-pos", "data": 100.0})
        await server.send({"event": "end-file", "reason": "eof"})
        await server.send({"event": "property-change", "name": "playlist-pos", "data": 1})
        await server.send({"event": "file-loaded"})
        await _finish_single_item(server)
        result = await task
    finally:
        await _close_playback_task(task, server)

    assert result.outcome is PlaybackOutcome.COMPLETED
    assert catalog.advance_count == 1
    assert catalog.completed_positions == [0, 1]
    assert catalog.session.context.kind is PlaybackContextKind.WATCH_ORDER


async def test_player_reports_pause_and_explicit_backward_seek(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    catalog = FakeKatalogClient(_session((_entry(0),)))
    server = FakeMpvIpcServer()
    _patch_mpv(monkeypatch, server)
    task = asyncio.create_task(MpvPlayerAgent(_settings(tmp_path), catalog).play(_TOKEN))
    try:
        await _await_connection(server)
        await server.send({"event": "property-change", "name": "time-pos", "data": 60.0})
        await server.send({"event": "property-change", "name": "pause", "data": True})
        await server.send({"event": "property-change", "name": "seeking", "data": True})
        await server.send({"event": "property-change", "name": "time-pos", "data": 10.0})
        await server.send({"event": "property-change", "name": "seeking", "data": False})
        await _wait_until(
            lambda: any(
                update.position_seconds == 10.0 and update.seek
                for _, update in catalog.progress_updates
            )
        )
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        await _close_playback_task(task, server)

    assert any(update.position_seconds == 60.0 for _, update in catalog.progress_updates)
    assert any(
        update.position_seconds == 10.0 and update.seek for _, update in catalog.progress_updates
    )
    assert catalog.completed_positions == []
    assert server.process.terminated
    assert list((tmp_path / "runtime").iterdir()) == []


async def test_player_does_not_mark_watched_after_an_mpv_crash(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    catalog = FakeKatalogClient(_session((_entry(0),)))
    server = FakeMpvIpcServer()
    _patch_mpv(monkeypatch, server)
    task = asyncio.create_task(MpvPlayerAgent(_settings(tmp_path), catalog).play(_TOKEN))
    try:
        await _await_connection(server)
        await server.send({"event": "property-change", "name": "time-pos", "data": 100.0})
        await server.send({"event": "end-file", "reason": "error"})
        server.process.finish(17)
        result = await task
    finally:
        await _close_playback_task(task, server)

    assert result.outcome is PlaybackOutcome.CRASHED
    assert catalog.completed_positions == []
    assert catalog.closed_session_ids == [catalog.session.id]


async def test_player_fails_cleanly_when_mpv_never_offers_ipc(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    catalog = FakeKatalogClient(_session((_entry(0),)))
    process = FakeMpvProcess()

    def fake_discover(_executable: str) -> Path:
        return Path("/fake/mpv")

    monkeypatch.setattr("kasana.kestrel.player.discover_mpv", fake_discover)

    async def launch_without_ipc(*_arguments: object, **_kwargs: object) -> FakeMpvProcess:
        return process

    monkeypatch.setattr(
        "kasana.kestrel.player.asyncio.create_subprocess_exec",
        cast(Callable[..., object], launch_without_ipc),
    )

    with pytest.raises(KestrelPlaybackError, match="IPC"):
        await MpvPlayerAgent(_settings(tmp_path, ipc_connect_timeout_seconds=0.05), catalog).play(
            _TOKEN
        )

    assert process.terminated
    assert catalog.closed_session_ids == [catalog.session.id]
    assert list((tmp_path / "runtime").iterdir()) == []


async def test_player_rejects_invalid_tokens_and_hides_catalog_failures(tmp_path: Path) -> None:
    catalog = FakeKatalogClient(_session((_entry(0),)))
    agent = MpvPlayerAgent(_settings(tmp_path), catalog)

    with pytest.raises(KestrelUriError):
        await agent.play("not a launch token")

    rejecting_agent = MpvPlayerAgent(_settings(tmp_path), RejectingKatalogClient(catalog.session))
    with pytest.raises(KestrelPlaybackError, match="Katalog could not launch") as error:
        await rejecting_agent.play(_TOKEN)

    assert "access token" not in str(error.value)

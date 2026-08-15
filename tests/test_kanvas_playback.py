"""Focused contracts for Kanvas's browser playback boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

import pytest
from _pytest.monkeypatch import MonkeyPatch
from pydantic import HttpUrl

from kasana.kanvas.services import playback as playback_service
from kasana.kanvas.services.playback import (
    KanvasPlaybackService,
    OptimisticWatchedState,
    playback_context,
)
from kasana.kanvas.settings import Kanvas_Settings
from kasana.katalog.public import (
    KatalogClientError,
    KatalogClientErrorKind,
    LibraryItemDetail,
    LibraryItemKind,
    ManualQueuePlaybackContext,
    PlaybackContext,
    PlaybackContextKind,
    PlaybackPlanEntry,
    PlaybackPlanLaunch,
    PlaybackPlanRequest,
    PlaybackSessionCloseResult,
    PlaybackSessionResponse,
    PlaybackSessionTrackSelection,
    PlaybackSessionTransitionRequest,
    SeriesPlaybackContext,
    SessionProgressUpdate,
    StandalonePlaybackContext,
    WatchOrderPlaybackContext,
)

_USER_ID = 13
_LAUNCH_TOKEN = "l" * 32
_SESSION_ID = "s" * 32


@dataclass
class _ClientState:
    item: LibraryItemDetail | None
    session: PlaybackSessionResponse
    launch: PlaybackPlanLaunch
    plan_requests: list[PlaybackPlanRequest] = field(default_factory=list)
    consumed_launch_tokens: list[str] = field(default_factory=list)
    progress_updates: list[tuple[str, SessionProgressUpdate]] = field(default_factory=list)
    track_selections: list[tuple[str, PlaybackSessionTrackSelection]] = field(default_factory=list)
    transitions: list[tuple[str, PlaybackSessionTransitionRequest]] = field(default_factory=list)
    closed_session_ids: list[str] = field(default_factory=list)


class _FakeClient:
    def __init__(self, state: _ClientState, _base_url: str, *, timeout_seconds: float) -> None:
        assert timeout_seconds == 8.0
        self._state = state

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_arguments: object) -> None:
        return None

    async def get_library_item(self, _item_id: int) -> SimpleNamespace:
        return SimpleNamespace(item=self._state.item)

    async def create_playback_plan(self, request: PlaybackPlanRequest) -> PlaybackPlanLaunch:
        self._state.plan_requests.append(request)
        return self._state.launch

    async def launch_playback_plan(self, launch_token: str) -> PlaybackSessionResponse:
        self._state.consumed_launch_tokens.append(launch_token)
        return self._state.session

    async def get_playback_session(self, _session_id: str) -> PlaybackSessionResponse:
        return self._state.session

    async def update_playback_session_progress(
        self, session_id: str, update: SessionProgressUpdate
    ) -> None:
        self._state.progress_updates.append((session_id, update))

    async def update_playback_session_tracks(
        self, session_id: str, selection: PlaybackSessionTrackSelection
    ) -> PlaybackSessionResponse:
        self._state.track_selections.append((session_id, selection))
        return self._state.session

    async def complete_and_advance_playback_session(
        self, session_id: str, request: PlaybackSessionTransitionRequest
    ) -> PlaybackSessionResponse:
        self._state.transitions.append((session_id, request))
        return self._state.session

    async def close_playback_session(self, session_id: str) -> PlaybackSessionCloseResult:
        self._state.closed_session_ids.append(session_id)
        current_item = self._state.session.current_item
        assert current_item is not None
        return PlaybackSessionCloseResult(
            current_entry_position=self._state.session.current_entry_position,
            current_item_id=current_item.item_id,
        )


def _settings() -> Kanvas_Settings:
    return Kanvas_Settings(
        katalog_url=HttpUrl("http://katalog.test"),
        session_secret="k" * 32,
    )


def _item(*, kind: LibraryItemKind, parent_id: int | None = None) -> LibraryItemDetail:
    return cast(LibraryItemDetail, SimpleNamespace(id=7, kind=kind, parent_id=parent_id))


def _entry(item_id: int, position: int) -> PlaybackPlanEntry:
    return PlaybackPlanEntry(
        position=position,
        item_id=item_id,
        display_title=f"Entry {item_id}",
        saved_resume_position_seconds=0,
        stream_url=f"/api/v1/media/{'m' * 32}",
        download_url=f"/api/v1/downloads/{'d' * 32}",
    )


def _session(
    *,
    user_id: int = _USER_ID,
    current_entry_position: int = 0,
    entries: tuple[PlaybackPlanEntry, ...] | None = None,
) -> PlaybackSessionResponse:
    entries = entries or (_entry(7, 0),)
    now = datetime.now(UTC)
    return PlaybackSessionResponse(
        id=_SESSION_ID,
        user_id=user_id,
        context=PlaybackContext(
            kind=PlaybackContextKind.STANDALONE,
            item_id=entries[0].item_id,
        ),
        current_entry_position=current_entry_position,
        current_item=(
            entries[current_entry_position] if current_entry_position < len(entries) else None
        ),
        entries=entries,
        created_at=now,
        expires_at=now + timedelta(hours=1),
        closed_at=None,
    )


def _launch() -> PlaybackPlanLaunch:
    return PlaybackPlanLaunch(launch_token=_LAUNCH_TOKEN, expires_at=datetime.now(UTC))


def _install_fake_client(monkeypatch: MonkeyPatch, state: _ClientState) -> None:
    def create_client(base_url: str, *, timeout_seconds: float) -> _FakeClient:
        return _FakeClient(state, base_url, timeout_seconds=timeout_seconds)

    monkeypatch.setattr(playback_service, "KatalogClient", create_client)


async def test_playback_service_builds_and_consumes_typed_item_and_order_plans(
    monkeypatch: MonkeyPatch,
) -> None:
    state = _ClientState(
        item=_item(kind=LibraryItemKind.SEASON, parent_id=44),
        session=_session(),
        launch=_launch(),
    )
    _install_fake_client(monkeypatch, state)
    service = KanvasPlaybackService(_settings(), _USER_ID)

    item_uri = await service.create_item_launch_uri(7, resume=True)
    watch_order_uri = await service.create_watch_order_launch_uri(
        21, start_item_id=7, resume=False, skip_unavailable=True
    )
    item_session = await service.create_item_playback_session(7, resume=True)
    watch_order_session = await service.create_watch_order_playback_session(
        21, start_item_id=7, skip_unavailable=True
    )

    assert item_uri == f"kasana://play/{_LAUNCH_TOKEN}"
    assert watch_order_uri == item_uri
    assert item_session == state.session
    assert watch_order_session == state.session
    assert len(state.consumed_launch_tokens) == 2
    item_context = state.plan_requests[0].context
    assert isinstance(item_context, SeriesPlaybackContext)
    assert item_context.series_id == 44
    assert item_context.resume is True
    watch_order_context = state.plan_requests[1].context
    assert isinstance(watch_order_context, WatchOrderPlaybackContext)
    assert watch_order_context.watch_order_id == 21
    assert watch_order_context.start_item_id == 7
    assert watch_order_context.skip_unavailable is True


async def test_episode_playback_starts_a_series_queue_at_the_selected_episode(
    monkeypatch: MonkeyPatch,
) -> None:
    state = _ClientState(
        item=_item(kind=LibraryItemKind.EPISODE),
        session=_session(),
        launch=_launch(),
    )
    _install_fake_client(monkeypatch, state)

    await KanvasPlaybackService(_settings(), _USER_ID).create_item_playback_session(
        7, resume=True
    )

    context = state.plan_requests[0].context
    assert isinstance(context, SeriesPlaybackContext)
    assert context.series_id is None
    assert context.episode_id == 7
    assert context.resume is False


async def test_playback_service_requires_owned_sessions_for_mutations_and_fallbacks(
    monkeypatch: MonkeyPatch,
) -> None:
    state = _ClientState(
        item=_item(kind=LibraryItemKind.MOVIE),
        session=_session(
            current_entry_position=1,
            entries=(_entry(7, 0), _entry(8, 1)),
        ),
        launch=_launch(),
    )
    _install_fake_client(monkeypatch, state)
    service = KanvasPlaybackService(_settings(), _USER_ID)
    progress = SessionProgressUpdate(position_seconds=21, expected_entry_position=1)
    selection = PlaybackSessionTrackSelection(
        expected_entry_position=1,
        audio_stream_index=2,
        subtitle_track_id="sidecar-0",
    )

    assert await service.playback_session(_SESSION_ID) == state.session
    await service.report_playback_progress(_SESSION_ID, progress)
    assert await service.select_playback_tracks(_SESSION_ID, selection) == state.session
    assert await service.complete_playback_entry(_SESSION_ID, 1) == state.session
    assert await service.close_playback_session(_SESSION_ID) == PlaybackSessionCloseResult(
        current_entry_position=1,
        current_item_id=8,
    )
    fallback_uri = await service.create_kestrel_fallback_uri(state.session)

    assert state.progress_updates == [(_SESSION_ID, progress)]
    assert state.track_selections == [(_SESSION_ID, selection)]
    assert state.transitions == [
        (_SESSION_ID, PlaybackSessionTransitionRequest(expected_entry_position=1))
    ]
    assert state.closed_session_ids == [_SESSION_ID]
    assert fallback_uri == f"kasana://play/{_LAUNCH_TOKEN}"
    fallback_context = state.plan_requests[-1].context
    assert isinstance(fallback_context, ManualQueuePlaybackContext)
    assert fallback_context.item_ids == (8,)

    state.session = _session(user_id=_USER_ID + 1)
    with pytest.raises(KatalogClientError) as error:
        await service.playback_session(_SESSION_ID)
    assert error.value.kind is KatalogClientErrorKind.NOT_FOUND


async def test_playback_service_rejects_unexpected_items_and_empty_fallbacks(
    monkeypatch: MonkeyPatch,
) -> None:
    state = _ClientState(item=None, session=_session(), launch=_launch())
    _install_fake_client(monkeypatch, state)
    service = KanvasPlaybackService(_settings(), _USER_ID)

    with pytest.raises(RuntimeError, match="unexpected empty item"):
        await service.create_item_launch_uri(7, resume=False)
    with pytest.raises(RuntimeError, match="unexpected empty item"):
        await service.create_item_playback_session(7, resume=False)
    with pytest.raises(ValueError, match="must contain a current media item"):
        await service.create_kestrel_fallback_uri(_session(current_entry_position=1))
    with pytest.raises(ValueError, match="requires a parent series"):
        playback_context(_item(kind=LibraryItemKind.SEASON), resume=False)
    movie_context = playback_context(_item(kind=LibraryItemKind.MOVIE), resume=True)
    assert isinstance(movie_context, StandalonePlaybackContext)
    assert movie_context.item_id == 7


def test_optimistic_watched_state_rejects_invalid_transition_order() -> None:
    state = OptimisticWatchedState(watched=False)

    with pytest.raises(RuntimeError, match="not pending"):
        state.commit()
    assert state.toggle() is True
    with pytest.raises(RuntimeError, match="already pending"):
        state.toggle()
    assert state.rollback() is False
    assert state.toggle() is True
    state.commit()
    with pytest.raises(RuntimeError, match="not pending"):
        state.rollback()

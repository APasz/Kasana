"""Kanvas playback actions using one-use Katalog launch tokens."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from kasana.kanvas.katalog_clients import katalog_client_context
from kasana.kanvas.services.katalog import is_series_like
from kasana.kanvas.settings import Kanvas_Settings
from kasana.katalog.public import (
    KatalogClient,
    KatalogClientError,
    KatalogClientErrorKind,
    LibraryItemDetail,
    LibraryItemKind,
    ManualQueuePlaybackContext,
    PlaybackPlanRequest,
    PlaybackSessionCloseResult,
    PlaybackSessionCompletionRequest,
    PlaybackSessionResponse,
    PlaybackSessionTrackSelection,
    PlaybackSessionTransitionRequest,
    SeriesPlaybackContext,
    SessionProgressUpdate,
    StandalonePlaybackContext,
    WatchOrderPlaybackContext,
)


class KanvasPlaybackService:
    """Creates safe Katalog playback plans for explicit UI actions."""

    def __init__(self, settings: Kanvas_Settings, user_id: int) -> None:
        self._settings = settings
        self._user_id = user_id

    @asynccontextmanager
    async def _client(self) -> AsyncGenerator[KatalogClient]:
        async with katalog_client_context(self._settings, client_factory=KatalogClient) as client:
            yield client

    async def create_item_launch_uri(self, item_id: int, *, resume: bool) -> str:
        """Create a one-use launch URI, without exposing a media URL to the browser."""

        async with self._client() as client:
            conditional_item = await client.get_library_item(item_id)
            if conditional_item.item is None:
                msg = "Katalog returned an unexpected empty item response."
                raise RuntimeError(msg)
            launch = await client.create_playback_plan(
                playback_plan_request(conditional_item.item, user_id=self._user_id, resume=resume)
            )
        return launch_uri(launch.launch_token)

    async def create_watch_order_launch_uri(
        self,
        watch_order_id: int,
        *,
        start_item_id: int | None = None,
        resume: bool = False,
        skip_unavailable: bool = False,
    ) -> str:
        """Launch an order while retaining its Katalog watch-order context."""

        async with self._client() as client:
            launch = await client.create_playback_plan(
                watch_order_playback_plan_request(
                    watch_order_id,
                    user_id=self._user_id,
                    start_item_id=start_item_id,
                    resume=resume,
                    skip_unavailable=skip_unavailable,
                )
            )
        return launch_uri(launch.launch_token)

    async def create_item_playback_session(
        self, item_id: int, *, resume: bool
    ) -> PlaybackSessionResponse:
        """Create and consume a browser-owned playback plan for one item or series."""

        async with self._client() as client:
            conditional_item = await client.get_library_item(item_id)
            if conditional_item.item is None:
                msg = "Katalog returned an unexpected empty item response."
                raise RuntimeError(msg)
            launch = await client.create_playback_plan(
                playback_plan_request(conditional_item.item, user_id=self._user_id, resume=resume)
            )
            return await client.launch_playback_plan(launch.launch_token)

    async def create_watch_order_playback_session(
        self,
        watch_order_id: int,
        *,
        start_item_id: int | None = None,
        resume: bool = False,
        skip_unavailable: bool = False,
    ) -> PlaybackSessionResponse:
        """Create and consume a browser-owned watch-order playback plan."""

        async with self._client() as client:
            launch = await client.create_playback_plan(
                watch_order_playback_plan_request(
                    watch_order_id,
                    user_id=self._user_id,
                    start_item_id=start_item_id,
                    resume=resume,
                    skip_unavailable=skip_unavailable,
                )
            )
            return await client.launch_playback_plan(launch.launch_token)

    async def playback_session(self, session_id: str) -> PlaybackSessionResponse:
        """Load a browser playback session, rejecting sessions owned by other profiles."""

        async with self._client() as client:
            session = await client.get_playback_session(session_id)
        return self._owned_session(session)

    async def report_playback_progress(
        self, session_id: str, update: SessionProgressUpdate
    ) -> None:
        """Record one browser progress sample after verifying session ownership."""

        async with self._client() as client:
            session = await client.get_playback_session(session_id)
            self._owned_session(session)
            await client.update_playback_session_progress(session_id, update)

    async def select_playback_tracks(
        self, session_id: str, selection: PlaybackSessionTrackSelection
    ) -> PlaybackSessionResponse:
        """Store a current-entry browser track choice after checking profile ownership."""

        async with self._client() as client:
            session = await client.get_playback_session(session_id)
            self._owned_session(session)
            selected = await client.update_playback_session_tracks(session_id, selection)
        return self._owned_session(selected)

    async def complete_playback_entry(
        self, session_id: str, expected_entry_position: int
    ) -> PlaybackSessionResponse:
        """Atomically complete the expected entry and transition its owned queue."""

        async with self._client() as client:
            session = await client.get_playback_session(session_id)
            self._owned_session(session)
            transitioned = await client.complete_and_advance_playback_session(
                session_id,
                PlaybackSessionTransitionRequest(expected_entry_position=expected_entry_position),
            )
        return self._owned_session(transitioned)

    async def complete_current_playback_entry(
        self, session_id: str, expected_entry_position: int
    ) -> None:
        """Explicitly complete an owned entry while leaving the queue position unchanged."""

        async with self._client() as client:
            session = await client.get_playback_session(session_id)
            self._owned_session(session)
            await client.complete_playback_session(
                session_id,
                PlaybackSessionCompletionRequest(expected_entry_position=expected_entry_position),
            )

    async def close_playback_session(self, session_id: str) -> PlaybackSessionCloseResult:
        """Close an owned browser session and return its final current entry."""

        async with self._client() as client:
            session = await client.get_playback_session(session_id)
            self._owned_session(session)
            return await client.close_playback_session(session_id)

    async def create_kestrel_fallback_uri(self, session: PlaybackSessionResponse) -> str:
        """Create a Kestrel launch for the unplayed tail of an owned browser queue."""

        owned_session = self._owned_session(session)
        remaining_entries = owned_session.entries[owned_session.current_entry_position :]
        entry_ids = tuple(entry.item_id for entry in remaining_entries)
        if not entry_ids:
            msg = "Playback sessions must contain a current media item."
            raise ValueError(msg)
        request = PlaybackPlanRequest(
            user_id=self._user_id,
            context=ManualQueuePlaybackContext(item_ids=entry_ids),
        )
        async with self._client() as client:
            launch = await client.create_playback_plan(request)
        return launch_uri(launch.launch_token)

    def _owned_session(self, session: PlaybackSessionResponse) -> PlaybackSessionResponse:
        if session.user_id != self._user_id:
            raise KatalogClientError(
                KatalogClientErrorKind.NOT_FOUND, "Playback session not found."
            )
        return session


@dataclass
class OptimisticWatchedState:
    """A reversible local watched-state mutation for a single visible item."""

    watched: bool
    _previous: bool | None = None

    def toggle(self) -> bool:
        """Apply the immediate UI state, rejecting concurrent duplicate actions."""

        if self._previous is not None:
            msg = "A watched-state update is already pending."
            raise RuntimeError(msg)
        self._previous = self.watched
        self.watched = not self.watched
        return self.watched

    def commit(self) -> None:
        """Keep the optimistic state after its Katalog mutation succeeded."""

        if self._previous is None:
            msg = "Cannot commit a watched-state update that is not pending."
            raise RuntimeError(msg)
        self._previous = None

    def rollback(self) -> bool:
        """Restore the former visible state after a failed Katalog mutation."""

        if self._previous is None:
            msg = "Cannot roll back a watched-state update that is not pending."
            raise RuntimeError(msg)
        self.watched = self._previous
        self._previous = None
        return self.watched


def playback_context(
    item: LibraryItemDetail, *, resume: bool
) -> StandalonePlaybackContext | SeriesPlaybackContext:
    """Choose the valid public Katalog context for a Kanvas item action."""

    if item.kind is LibraryItemKind.EPISODE:
        return SeriesPlaybackContext(episode_id=item.id)
    if not is_series_like(item.kind):
        return StandalonePlaybackContext(item_id=item.id)
    series_id = item.id if item.kind.value == "series" else item.parent_id
    if series_id is None:
        msg = "A season item requires a parent series for playback."
        raise ValueError(msg)
    return SeriesPlaybackContext(series_id=series_id, resume=resume)


def playback_plan_request(
    item: LibraryItemDetail, *, user_id: int, resume: bool
) -> PlaybackPlanRequest:
    """Build a typed public Katalog plan request for one item action."""

    return PlaybackPlanRequest(user_id=user_id, context=playback_context(item, resume=resume))


def watch_order_playback_plan_request(
    watch_order_id: int,
    *,
    user_id: int,
    start_item_id: int | None = None,
    resume: bool = False,
    skip_unavailable: bool = False,
) -> PlaybackPlanRequest:
    """Build an order-aware plan request for play and play-from-here controls."""

    return PlaybackPlanRequest(
        user_id=user_id,
        context=WatchOrderPlaybackContext(
            watch_order_id=watch_order_id,
            start_item_id=start_item_id,
            resume=resume,
            skip_unavailable=skip_unavailable,
        ),
    )


def launch_uri(launch_token: str) -> str:
    """Build the only browser-visible playback identifier."""

    return f"kasana://play/{launch_token}"

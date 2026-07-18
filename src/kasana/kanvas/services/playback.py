"""Kanvas playback actions using one-use Katalog launch tokens."""

from __future__ import annotations

from dataclasses import dataclass

from kasana.kanvas.services.katalog import is_series_like
from kasana.kanvas.settings import Kanvas_Settings
from kasana.katalog.public import (
    KatalogClient,
    LibraryItemDetail,
    PlaybackPlanRequest,
    SeriesPlaybackContext,
    StandalonePlaybackContext,
)


class KanvasPlaybackService:
    """Creates safe Katalog playback plans for explicit UI actions."""

    def __init__(self, settings: Kanvas_Settings) -> None:
        self._settings = settings

    async def create_item_launch_uri(self, item_id: int, *, resume: bool) -> str:
        """Create a one-use launch URI, without exposing a media URL to the browser."""

        async with KatalogClient(
            str(self._settings.katalog_url), timeout_seconds=self._settings.katalog_timeout_seconds
        ) as client:
            conditional_item = await client.get_library_item(item_id)
            if conditional_item.item is None:
                msg = "Katalog returned an unexpected empty item response."
                raise RuntimeError(msg)
            launch = await client.create_playback_plan(
                playback_plan_request(
                    conditional_item.item, user_id=self._settings.user_id, resume=resume
                )
            )
        return launch_uri(launch.launch_token)


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


def launch_uri(launch_token: str) -> str:
    """Build the only browser-visible playback identifier."""

    return f"kasana://play/{launch_token}"

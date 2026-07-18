"""The sole Kanvas boundary around Katalog's supported public client."""

from __future__ import annotations

from asyncio import gather
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from kasana.kanvas.settings import Kanvas_Settings
from kasana.kanvas.viewmodels.home import MediaRailView
from kasana.kanvas.viewmodels.item import ItemDetailView
from kasana.kanvas.viewmodels.library import LibraryFilters, PosterState, PosterView
from kasana.katalog.public import (
    ArtworkKind,
    ArtworkSelection,
    Availability,
    KatalogClient,
    LibraryItemKind,
    LibraryItemSummary,
    PlaybackStateResponse,
)

_RAIL_PAGE_SIZE = 20
_GRID_PAGE_SIZE = 48
_DETAIL_CHILD_PAGE_SIZE = 50


class KanvasKatalogService:
    """Transforms Katalog contracts into safe, purpose-specific Kanvas data."""

    def __init__(self, settings: Kanvas_Settings) -> None:
        self._settings = settings

    @asynccontextmanager
    async def _client(self) -> AsyncGenerator[KatalogClient]:
        async with KatalogClient(
            str(self._settings.katalog_url), timeout_seconds=self._settings.katalog_timeout_seconds
        ) as client:
            yield client

    async def home_rails(self) -> tuple[MediaRailView, ...]:
        """Load the three small, real-data home rails concurrently at the HTTP layer."""

        async with self._client() as client:
            continue_page, on_deck_page, added_page = await gather(
                client.continue_watching(self._settings.user_id, limit=_RAIL_PAGE_SIZE),
                client.on_deck(self._settings.user_id, limit=_RAIL_PAGE_SIZE),
                client.list_library_items(limit=_RAIL_PAGE_SIZE),
            )

        return (
            MediaRailView(
                title="Continue",
                posters=tuple(
                    poster_from_summary(entry.item, playback=entry.playback)
                    for entry in continue_page.items
                ),
            ),
            MediaRailView(
                title="On Deck",
                posters=tuple(poster_from_summary(entry.item) for entry in on_deck_page.items),
            ),
            MediaRailView(
                title="Added",
                posters=tuple(poster_from_summary(item) for item in added_page.items),
            ),
        )

    async def library_page(
        self, filters: LibraryFilters, *, cursor: str | None
    ) -> tuple[tuple[PosterView, ...], str | None]:
        """Load one deliberately bounded poster page."""

        async with self._client() as client:
            page = await client.list_library_items(
                cursor=cursor,
                limit=_GRID_PAGE_SIZE,
                kind=filters.kind,
                tags=("anime",) if filters.anime else (),
                year=filters.year,
                watched=filters.watched,
                user_id=self._settings.user_id if filters.watched is not None else None,
                availability=filters.availability,
                search=filters.search,
            )
        return tuple(poster_from_summary(item) for item in page.items), page.next_cursor

    async def item_detail(self, item_id: int) -> ItemDetailView:
        """Create a safe item view without exposing Katalog playback or media URLs."""

        async with self._client() as client:
            conditional_item = await client.get_library_item(item_id)
            if conditional_item.item is None:
                msg = "Katalog returned an unexpected empty item response."
                raise RuntimeError(msg)
            item = conditional_item.item
            media_page = await client.list_library_item_media(item_id, limit=1)
            children_page = await client.list_library_item_children(
                item_id, limit=_DETAIL_CHILD_PAGE_SIZE
            )
            playback = await _playback_for_item(client, self._settings.user_id, item_id)

        return ItemDetailView(
            id=item.id,
            title=item.title,
            kind=item.kind.value,
            year=item.year,
            overview=item.overview,
            posterUrl=artwork_proxy_url(item.id, item.artwork, ArtworkKind.POSTER),
            backdropUrl=artwork_proxy_url(item.id, item.artwork, ArtworkKind.BACKDROP),
            runtimeLabel=runtime_label(media_page.items[0].duration_seconds)
            if media_page.items
            else None,
            progressPercent=progress_percent(playback),
            watched=playback.completed if playback is not None else False,
            available=item.availability is Availability.AVAILABLE,
            children=tuple(poster_from_summary(child) for child in children_page.items),
        )

    async def mark_watched(self, item_id: int) -> None:
        """Mark an item watched through Katalog's public mutation contract."""

        async with self._client() as client:
            await client.mark_watched(self._settings.user_id, item_id)

    async def clear_watched(self, item_id: int) -> None:
        """Clear watched state through Katalog's public mutation contract."""

        async with self._client() as client:
            await client.clear_watched(self._settings.user_id, item_id)

    async def artwork_content(self, item_id: int, artwork_id: int) -> tuple[bytes, str, str | None]:
        """Fetch artwork server-side so browser clients never learn Katalog's origin."""

        artwork_url = f"/api/v1/library/items/{item_id}/artwork/{artwork_id}"
        async with self._client() as client:
            artwork = await client.get_artwork_content(artwork_url)
        if artwork is None:
            msg = "Katalog returned a conditional artwork response without an entity tag."
            raise RuntimeError(msg)
        return artwork.content, artwork.content_type, artwork.etag


async def _playback_for_item(
    client: KatalogClient, user_id: int, item_id: int
) -> PlaybackStateResponse | None:
    """Use the existing progress query without turning a missing state into an error."""

    page = await client.continue_watching(user_id, limit=100)
    for entry in page.items:
        if entry.item.id == item_id:
            return entry.playback
    return None


def poster_from_summary(
    item: LibraryItemSummary,
    *,
    playback: PlaybackStateResponse | None = None,
    selected: bool = False,
    loading: bool = False,
) -> PosterView:
    """Translate a Katalog summary to the single poster visual contract."""

    poster_url = artwork_proxy_url(item.id, item.artwork, ArtworkKind.POSTER)
    state = poster_state(
        available=item.availability is Availability.AVAILABLE,
        has_artwork=poster_url is not None,
        playback=playback,
        selected=selected,
        loading=loading,
    )
    subtitle = " · ".join(part for part in (item.year and str(item.year), item.kind.value) if part)
    return PosterView(
        id=item.id,
        title=item.title,
        subtitle=subtitle or None,
        href=f"/item/{item.id}",
        posterUrl=poster_url,
        progressPercent=progress_percent(playback),
        state=state,
        available=item.availability is Availability.AVAILABLE,
    )


def poster_state(
    *,
    available: bool,
    has_artwork: bool,
    playback: PlaybackStateResponse | None,
    selected: bool,
    loading: bool,
) -> PosterState:
    """Resolve poster precedence in one reusable, testable place."""

    if loading:
        return PosterState.LOADING
    if selected:
        return PosterState.SELECTED
    if not available:
        return PosterState.UNAVAILABLE
    if not has_artwork:
        return PosterState.MISSING_ARTWORK
    if playback is not None and playback.completed:
        return PosterState.WATCHED
    if progress_percent(playback) is not None:
        return PosterState.IN_PROGRESS
    return PosterState.NORMAL


def artwork_proxy_url(
    item_id: int, artwork: tuple[ArtworkSelection, ...], kind: ArtworkKind
) -> str | None:
    """Return a same-origin artwork route, never a provider or filesystem location."""

    selected = next((entry for entry in artwork if entry.kind is kind), None)
    return f"/kanvas/artwork/{item_id}/{selected.id}" if selected is not None else None


def progress_percent(playback: PlaybackStateResponse | None) -> int | None:
    """Bound progress for stable one-pixel poster indicators."""

    if playback is None or playback.duration_seconds <= 0 or playback.completed:
        return None
    return min(100, max(0, round(playback.position_seconds / playback.duration_seconds * 100)))


def runtime_label(duration_seconds: float | None) -> str | None:
    """Format an optional media runtime without leaking technical media details."""

    if duration_seconds is None:
        return None
    total_minutes = round(duration_seconds / 60)
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


def is_series_like(kind: LibraryItemKind) -> bool:
    """Identify item kinds that use the series playback-plan context."""

    return kind in {LibraryItemKind.SERIES, LibraryItemKind.SEASON}

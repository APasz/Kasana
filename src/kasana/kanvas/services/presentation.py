"""Pure transformations from Katalog contracts to Kanvas presentation models."""

from __future__ import annotations

import re

from kasana.kanvas.viewmodels.collections import (
    CollectionMemberView,
    CollectionTileView,
    ItemPickerView,
    WatchOrderCardView,
    WatchOrderRowView,
)
from kasana.kanvas.viewmodels.library import PlaceholderArtView, PosterState, PosterView
from kasana.katalog.public import (
    ArtworkKind,
    ArtworkSelection,
    Availability,
    CollectionDetail,
    CollectionRelationship,
    CollectionUpdate,
    LibraryItemKind,
    LibraryItemSummary,
    PlaybackStateResponse,
    WatchOrderDetail,
    WatchOrderEntryDetail,
    WatchOrderKind,
    WatchOrderUpdate,
)

_ARTWORK_URL = re.compile(
    r"^/api/v1/library/items/(?P<item_id>\d+)/artwork/(?P<artwork_id>\d+)$"
)
_GENERIC_EPISODE_TITLE = re.compile(r"^(?:episode|ep\.?)\s*(?P<number>\d+)$", re.IGNORECASE)


PLAYABLE_KINDS = frozenset(
    {
        LibraryItemKind.MOVIE,
        LibraryItemKind.EPISODE,
        LibraryItemKind.SPECIAL,
        LibraryItemKind.EXTRA,
    }
)


def collection_tile(detail: CollectionDetail) -> CollectionTileView:
    """Transform one detail contract into a compact, artwork-safe grid tile."""

    posters = tuple(poster_from_summary(member.item) for member in detail.members)
    artwork_url, mosaic_urls = collection_artwork(detail, posters)
    return CollectionTileView(
        id=detail.id,
        name=detail.name,
        itemCount=detail.item_count,
        watchOrderCount=detail.watch_order_count,
        revision=detail.revision,
        artworkUrl=artwork_url,
        mosaicUrls=mosaic_urls,
    )


def collection_member(
    item: LibraryItemSummary,
    relationship: CollectionRelationship | None,
    progress: dict[int, PlaybackStateResponse],
) -> CollectionMemberView:
    """Create one direct-member presentation record without traversing children."""

    return CollectionMemberView(
        poster=poster_from_summary(item, playback=progress.get(item.id)),
        kind=item.kind.value,
        relationship=relationship.value if relationship is not None else None,
    )


def group_collection_members(
    members: tuple[CollectionMemberView, ...],
) -> tuple[
    tuple[CollectionMemberView, ...],
    tuple[CollectionMemberView, ...],
    tuple[CollectionMemberView, ...],
]:
    """Group direct members by media shape while leaving cultures and genres unconstrained."""

    movies = tuple(member for member in members if member.kind == LibraryItemKind.MOVIE.value)
    series = tuple(member for member in members if member.kind == LibraryItemKind.SERIES.value)
    selected = {member.poster.id for member in movies + series}
    other = tuple(member for member in members if member.poster.id not in selected)
    return movies, series, other


def collection_artwork(
    detail: CollectionDetail, members: tuple[PosterView, ...]
) -> tuple[str | None, tuple[str, ...]]:
    """Prefer explicit artwork, then a stable direct-member poster mosaic."""

    if detail.representative_artwork is not None:
        explicit = artwork_proxy_from_api_url(detail.representative_artwork.url)
        if explicit is not None:
            return explicit, ()
    mosaic = tuple(
        poster.poster_url
        for poster in members
        if poster.poster_url is not None and poster.available
    )[:4]
    return None, mosaic


def watch_order_card(
    detail: WatchOrderDetail, progress: dict[int, PlaybackStateResponse]
) -> WatchOrderCardView:
    """Derive a compact watch-order card from one bounded public detail response."""

    next_entry = next(
        (
            entry
            for entry in detail.entries.items
            if not progress.get(entry.item.id, None) or not progress[entry.item.id].completed
        ),
        None,
    )
    next_playback = progress.get(next_entry.item.id) if next_entry is not None else None
    return WatchOrderCardView(
        id=detail.watch_order.id,
        collectionId=detail.watch_order.collection_id,
        name=detail.watch_order.name,
        kind=detail.watch_order.kind.value,
        entryCount=detail.watch_order.entry_count,
        revision=detail.watch_order.revision,
        progressPercent=progress_percent(next_playback),
        nextItemTitle=next_entry.item.title if next_entry is not None else None,
        hasUnavailableEntries=any(
            entry.item.availability is not Availability.AVAILABLE for entry in detail.entries.items
        ),
    )


def item_picker_view(item: LibraryItemSummary, *, already_member: bool) -> ItemPickerView:
    """Translate a library search hit without leaking its playback URL."""

    return ItemPickerView(
        id=item.id,
        title=item.title,
        kind=item.kind.value,
        year=item.year,
        available=item.availability is Availability.AVAILABLE,
        alreadyMember=already_member,
        posterUrl=artwork_proxy_url(item.id, item.artwork, ArtworkKind.POSTER),
    )


def watch_order_row(entry: WatchOrderEntryDetail) -> WatchOrderRowView:
    """Map a typed entry into browser-virtualised dense-row data."""

    item = entry.item
    return WatchOrderRowView(
        id=entry.id,
        position=entry.position,
        itemId=item.id,
        title=item.title,
        kind=item.kind.value,
        year=item.year,
        available=item.availability is Availability.AVAILABLE,
        posterUrl=artwork_proxy_url(item.id, item.artwork, ArtworkKind.POSTER),
    )


def generated_row(item: LibraryItemSummary, position: int) -> WatchOrderRowView:
    """Render a generated item with a stable preview-only row identity."""

    return WatchOrderRowView(
        id=position + 1,
        position=position,
        itemId=item.id,
        title=item.title,
        kind=item.kind.value,
        year=item.year,
        available=item.availability is Availability.AVAILABLE,
        posterUrl=artwork_proxy_url(item.id, item.artwork, ArtworkKind.POSTER),
    )


def artwork_proxy_from_api_url(url: str) -> str | None:
    """Translate only an expected Katalog artwork URL into a same-origin proxy URL."""

    match = _ARTWORK_URL.fullmatch(url)
    if match is None:
        return None
    return f"/kanvas/artwork/{match['item_id']}/{match['artwork_id']}"


def collection_update_request(
    *, revision: int, name: str | None, overview: str | None
) -> CollectionUpdate:
    if name is not None and overview is not None:
        return CollectionUpdate(expected_revision=revision, name=name, overview=overview)
    if name is not None:
        return CollectionUpdate(expected_revision=revision, name=name)
    return CollectionUpdate(expected_revision=revision, overview=overview)


def watch_order_update_request(
    *, revision: int, name: str | None, kind: WatchOrderKind | None
) -> WatchOrderUpdate:
    if name is not None and kind is not None:
        return WatchOrderUpdate(expected_revision=revision, name=name, kind=kind)
    if name is not None:
        return WatchOrderUpdate(expected_revision=revision, name=name)
    if kind is not None:
        return WatchOrderUpdate(expected_revision=revision, kind=kind)
    return WatchOrderUpdate(expected_revision=revision)


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
        placeholder=placeholder_art_for_summary(item),
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


def placeholder_art_for_summary(item: LibraryItemSummary) -> PlaceholderArtView:
    """Build deterministic missing-poster text from the strongest known item label."""

    if item.kind is LibraryItemKind.EPISODE and _is_generic_episode_title(
        item.title, item.episode_number, item.series_title
    ):
        episode_label = (
            f"Episode {item.episode_number}" if item.episode_number is not None else "Episode"
        )
        return PlaceholderArtView(
            lines=(episode_label,),
            footer=item.context_label,
        )
    return PlaceholderArtView(lines=placeholder_title_lines(item.title), footer=item.context_label)


def placeholder_title_lines(title: str) -> tuple[str, ...]:
    """Split a title/subtitle pair into separate generated-art text lines."""

    primary, separator, secondary = title.partition(":")
    if separator and primary.strip() and secondary.strip():
        return (primary.strip(), secondary.strip())
    return (title.strip(),)


def _is_generic_episode_title(
    title: str, episode_number: int | None, series_title: str | None
) -> bool:
    if series_title is not None and title.strip().casefold() == series_title.strip().casefold():
        return True
    if episode_number is None:
        return False
    match = _GENERIC_EPISODE_TITLE.fullmatch(title.strip())
    return match is not None and int(match["number"]) == episode_number


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

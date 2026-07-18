"""Safe Kanvas presentation models for collections and watch orders."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from kasana.kanvas.viewmodels.library import PosterView


class CollectionTileView(BaseModel):
    """One bounded collection-grid tile without provider or filesystem URLs."""

    model_config = ConfigDict(frozen=True)

    id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=1_000)
    item_count: int = Field(ge=0, alias="itemCount")
    watch_order_count: int = Field(ge=0, alias="watchOrderCount")
    revision: int = Field(ge=1)
    artwork_url: str | None = Field(default=None, alias="artworkUrl")
    mosaic_urls: tuple[str, ...] = Field(default=(), max_length=4, alias="mosaicUrls")


class CollectionMemberView(BaseModel):
    """A direct member and its optional collection relationship."""

    model_config = ConfigDict(frozen=True)

    poster: PosterView
    kind: str = Field(min_length=1, max_length=32)
    relationship: str | None = Field(default=None, max_length=32)


class WatchOrderCardView(BaseModel):
    """Compact collection-detail summary with derived playback cues."""

    model_config = ConfigDict(frozen=True)

    id: int = Field(gt=0)
    collection_id: int = Field(gt=0, alias="collectionId")
    name: str = Field(min_length=1, max_length=1_000)
    kind: str = Field(min_length=1, max_length=32)
    entry_count: int = Field(ge=0, alias="entryCount")
    revision: int = Field(ge=1)
    progress_percent: int | None = Field(default=None, ge=0, le=100, alias="progressPercent")
    next_item_title: str | None = Field(default=None, max_length=1_000, alias="nextItemTitle")
    has_unavailable_entries: bool = Field(default=False, alias="hasUnavailableEntries")


class CollectionDetailView(BaseModel):
    """A bounded collection detail page, grouped only by direct member kind."""

    model_config = ConfigDict(frozen=True)

    id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=1_000)
    overview: str | None = Field(default=None, max_length=20_000)
    item_count: int = Field(ge=0, alias="itemCount")
    watch_order_count: int = Field(ge=0, alias="watchOrderCount")
    revision: int = Field(ge=1)
    artwork_url: str | None = Field(default=None, alias="artworkUrl")
    mosaic_urls: tuple[str, ...] = Field(default=(), max_length=4, alias="mosaicUrls")
    movies: tuple[CollectionMemberView, ...] = ()
    series: tuple[CollectionMemberView, ...] = ()
    other_members: tuple[CollectionMemberView, ...] = Field(default=(), alias="otherMembers")
    member_next_cursor: str | None = Field(default=None, max_length=500, alias="memberNextCursor")
    watch_orders: tuple[WatchOrderCardView, ...] = Field(
        default=(), max_length=100, alias="watchOrders"
    )


class ItemPickerView(BaseModel):
    """One search result for an editor overlay, bounded by the Katalog cursor."""

    model_config = ConfigDict(frozen=True)

    id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=1_000)
    kind: str = Field(min_length=1, max_length=32)
    year: int | None = Field(default=None, ge=1, le=9999)
    available: bool
    already_member: bool = Field(alias="alreadyMember")
    poster_url: str | None = Field(default=None, alias="posterUrl")


class WatchOrderRowView(BaseModel):
    """A dense, serialisable entry row for the virtual browser component."""

    model_config = ConfigDict(frozen=True)

    id: int = Field(gt=0)
    position: int = Field(ge=0)
    item_id: int = Field(gt=0, alias="itemId")
    title: str = Field(min_length=1, max_length=1_000)
    kind: str = Field(min_length=1, max_length=32)
    year: int | None = Field(default=None, ge=1, le=9999)
    available: bool
    poster_url: str | None = Field(default=None, alias="posterUrl")


class WatchOrderEditorView(BaseModel):
    """Header state for a watch-order editor with separately paged rows."""

    model_config = ConfigDict(frozen=True)

    id: int = Field(gt=0)
    collection_id: int = Field(gt=0, alias="collectionId")
    collection_name: str = Field(min_length=1, max_length=1_000, alias="collectionName")
    name: str = Field(min_length=1, max_length=1_000)
    kind: str = Field(min_length=1, max_length=32)
    entry_count: int = Field(ge=0, alias="entryCount")
    revision: int = Field(ge=1)


class GenerationPreviewView(BaseModel):
    """Explicit generation decision data rendered before any mutation."""

    model_config = ConfigDict(frozen=True)

    watch_order_id: int = Field(gt=0, alias="watchOrderId")
    revision: int = Field(ge=1)
    mode: str = Field(min_length=1, max_length=32)
    apply_mode: str = Field(min_length=1, max_length=32, alias="applyMode")
    entries: tuple[WatchOrderRowView, ...]
    undated_titles: tuple[str, ...] = Field(default=(), alias="undatedTitles")
    unavailable_titles: tuple[str, ...] = Field(default=(), alias="unavailableTitles")
    duplicate_titles: tuple[str, ...] = Field(default=(), alias="duplicateTitles")
    non_playable_titles: tuple[str, ...] = Field(default=(), alias="nonPlayableTitles")
    removed_entry_titles: tuple[str, ...] = Field(default=(), alias="removedEntryTitles")

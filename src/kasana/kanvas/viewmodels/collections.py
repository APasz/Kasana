"""Safe Kanvas presentation models for collections and watch orders."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from kasana.kanvas.viewmodels.library import PosterView
from kasana.katalog.public import (
    MAX_WATCH_ORDER_ENTRIES,
    LibraryItemKind,
)


class CollectionMembershipStateView(BaseModel):
    """The action available for an item while browsing a collection."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    item_id: int = Field(gt=0, alias="itemId")
    state: Literal["absent", "direct", "inherited"]
    inherited_from_id: int | None = Field(default=None, gt=0, alias="inheritedFromId")
    inherited_from_title: str | None = Field(default=None, alias="inheritedFromTitle")


class CollectionModeView(BaseModel):
    """Collection identity and a bounded page of membership controls."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: int = Field(gt=0)
    name: str
    item_count: int = Field(ge=0, alias="itemCount")
    revision: int = Field(ge=1)
    artwork_item_id: int | None = Field(default=None, gt=0, alias="artworkItemId")
    items: tuple[CollectionMembershipStateView, ...] = ()


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
    """A direct member of a collection."""

    model_config = ConfigDict(frozen=True)

    poster: PosterView
    kind: str = Field(min_length=1, max_length=32)


class WatchOrderCardView(BaseModel):
    """Compact collection-detail summary with derived playback cues."""

    model_config = ConfigDict(frozen=True)

    id: int = Field(gt=0)
    collection_id: int = Field(gt=0, alias="collectionId")
    name: str = Field(min_length=1, max_length=1_000)
    entry_count: int = Field(ge=0, alias="entryCount")
    revision: int = Field(ge=1)
    is_default: bool = Field(default=False, alias="isDefault")
    completed_entry_count: int | None = Field(default=None, ge=0, alias="completedEntryCount")
    progress_percent: int | None = Field(default=None, ge=0, le=100, alias="progressPercent")
    next_item_title: str | None = Field(default=None, max_length=1_000, alias="nextItemTitle")
    has_unavailable_entries: bool = Field(default=False, alias="hasUnavailableEntries")


class CollectionDetailView(BaseModel):
    """A collection page view, grouped only by direct member kind."""

    model_config = ConfigDict(frozen=True)

    id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=1_000)
    overview: str | None = Field(default=None, max_length=20_000)
    item_count: int = Field(ge=0, alias="itemCount")
    watch_order_count: int = Field(ge=0, alias="watchOrderCount")
    revision: int = Field(ge=1)
    artwork_item_id: int | None = Field(default=None, gt=0, alias="artworkItemId")
    default_watch_order_id: int | None = Field(default=None, gt=0, alias="defaultWatchOrderId")
    artwork_url: str | None = Field(default=None, alias="artworkUrl")
    mosaic_urls: tuple[str, ...] = Field(default=(), max_length=4, alias="mosaicUrls")
    movies: tuple[CollectionMemberView, ...] = ()
    series: tuple[CollectionMemberView, ...] = ()
    other_members: tuple[CollectionMemberView, ...] = Field(default=(), alias="otherMembers")
    member_next_cursor: str | None = Field(default=None, max_length=500, alias="memberNextCursor")
    watch_orders: tuple[WatchOrderCardView, ...] = Field(default=(), alias="watchOrders")


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


class WatchOrderItemView(BaseModel):
    """Media identity shared by order entries and the source picker."""

    model_config = ConfigDict(frozen=True)

    title: str = Field(min_length=1, max_length=1_000)
    kind: LibraryItemKind
    year: int | None = Field(default=None, ge=1, le=9999)
    parent_id: int | None = Field(default=None, gt=0, alias="parentId")
    series_title: str | None = Field(default=None, max_length=1_000, alias="seriesTitle")
    season_number: int | None = Field(default=None, ge=0, alias="seasonNumber")
    episode_number: int | None = Field(default=None, ge=0, alias="episodeNumber")
    episode_end_number: int | None = Field(default=None, ge=0, alias="episodeEndNumber")
    episode_end_season_number: int | None = Field(
        default=None, ge=0, alias="episodeEndSeasonNumber"
    )
    available: bool


class WatchOrderRowView(WatchOrderItemView):
    """One explicit, ordered library item."""

    id: int = Field(gt=0)
    position: int = Field(ge=0)
    item_id: int = Field(gt=0, alias="itemId")
    poster_url: str | None = Field(default=None, alias="posterUrl")
    poster: PosterView | None = None


class WatchOrderSourceView(WatchOrderItemView):
    """A collection item that may add itself or its playable descendants as one block."""

    model_config = ConfigDict(frozen=True)

    id: int = Field(gt=0)
    entry_count: int = Field(ge=0, le=MAX_WATCH_ORDER_ENTRIES, alias="entryCount")
    item_ids: tuple[int, ...] = Field(
        default=(), max_length=MAX_WATCH_ORDER_ENTRIES, alias="itemIds"
    )
    addable: bool
    poster: PosterView


class WatchOrderWorkspaceView(BaseModel):
    """The editable order and eligible collection sources in one browser payload."""

    model_config = ConfigDict(frozen=True)

    revision: int = Field(ge=1)
    name: str = ""
    entry_limit: int = Field(default=MAX_WATCH_ORDER_ENTRIES, alias="entryLimit")
    entries: tuple[WatchOrderRowView, ...] = Field(default=(), max_length=MAX_WATCH_ORDER_ENTRIES)
    sources: tuple[WatchOrderSourceView, ...] = ()


class WatchOrderEditorView(BaseModel):
    """Header state for a watch-order editor with separately paged rows."""

    model_config = ConfigDict(frozen=True)

    id: int = Field(gt=0)
    collection_id: int = Field(gt=0, alias="collectionId")
    collection_name: str = Field(min_length=1, max_length=1_000, alias="collectionName")
    name: str = Field(min_length=1, max_length=1_000)
    entry_count: int = Field(ge=0, alias="entryCount")
    revision: int = Field(ge=1)


class GenerationPreviewView(BaseModel):
    """Explicit generation decision data rendered before any mutation."""

    model_config = ConfigDict(frozen=True)

    watch_order_id: int = Field(gt=0, alias="watchOrderId")
    revision: int = Field(ge=1)
    apply_mode: str = Field(min_length=1, max_length=32, alias="applyMode")
    entries: tuple[WatchOrderRowView, ...]
    undated_titles: tuple[str, ...] = Field(default=(), alias="undatedTitles")
    undated_item_ids: tuple[int, ...] = Field(default=(), alias="undatedItemIds")
    unavailable_titles: tuple[str, ...] = Field(default=(), alias="unavailableTitles")
    duplicate_titles: tuple[str, ...] = Field(default=(), alias="duplicateTitles")
    non_playable_titles: tuple[str, ...] = Field(default=(), alias="nonPlayableTitles")
    removed_entry_titles: tuple[str, ...] = Field(default=(), alias="removedEntryTitles")
    preview_token: str | None = Field(default=None, alias="previewToken")

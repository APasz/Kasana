"""The sole Kanvas boundary around Katalog's supported public client."""

from __future__ import annotations

import logging
from asyncio import gather
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal

from kasana.kanvas.services.presentation import (
    PLAYABLE_KINDS,
    artwork_proxy_from_api_url,
    artwork_proxy_url,
    collection_artwork,
    collection_member,
    collection_tile,
    collection_update_request,
    generated_row,
    group_collection_members,
    is_series_like,
    item_picker_view,
    placeholder_art_for_summary,
    placeholder_title_lines,
    poster_from_summary,
    poster_state,
    progress_percent,
    runtime_label,
    watch_order_card,
    watch_order_row,
    watch_order_update_request,
)
from kasana.kanvas.settings import Kanvas_Settings
from kasana.kanvas.viewmodels.administration import (
    AdministrationOverviewView,
    JobView,
    LibraryRootView,
    MetadataReviewItemView,
    job_view,
    library_root_view,
    metadata_candidate_view,
    overview_from_status,
)
from kasana.kanvas.viewmodels.collections import (
    CollectionDetailView,
    CollectionTileView,
    GenerationPreviewView,
    ItemPickerView,
    WatchOrderEditorView,
    WatchOrderRowView,
)
from kasana.kanvas.viewmodels.home import MediaRailView
from kasana.kanvas.viewmodels.item import ItemDetailView
from kasana.kanvas.viewmodels.library import (
    LibraryFilters,
    PosterView,
)
from kasana.katalog.public import (
    ArtworkFetchRequest,
    ArtworkKind,
    Availability,
    CollectionCreate,
    CollectionMembershipCreate,
    CollectionMembershipUpdate,
    CollectionRelationship,
    DirectoryListing,
    HierarchyRepairPreview,
    HierarchyRepairRequest,
    KatalogClient,
    LibraryConsistencyRequest,
    LibraryItemDetail,
    LibraryItemEditAudit,
    LibraryItemKind,
    LibraryItemMutationResult,
    LibraryItemSummary,
    LibraryItemUpdate,
    LibraryRootCreate,
    LibraryRootSummary,
    LibraryRootUpdate,
    MetadataMatchRequest,
    MetadataRejectRequest,
    MetadataReviewCandidate,
    PaginatedResponse,
    PlaybackStateResponse,
    ScanRequest,
    WatchOrderCreate,
    WatchOrderEntryCreate,
    WatchOrderEntryDetail,
    WatchOrderEntryMove,
    WatchOrderGenerationApplyMode,
    WatchOrderGenerationMode,
    WatchOrderGenerationRequest,
    WatchOrderKind,
)

_RAIL_PAGE_SIZE = 20
_GRID_PAGE_SIZE = 48
_DETAIL_CHILD_PAGE_SIZE = 50
_COLLECTION_GRID_PAGE_SIZE = 24
_COLLECTION_MEMBER_PAGE_SIZE = 100
_WATCH_ORDER_PAGE_SIZE = 50
_WATCH_ORDER_ENTRY_PAGE_SIZE = 100
_PICKER_PAGE_SIZE = 48
_LOGGER = logging.getLogger(__name__)

__all__ = (
    "KanvasKatalogService",
    "LibraryPosterTransformationError",
    "OptimisticRevisionState",
    "artwork_proxy_from_api_url",
    "artwork_proxy_url",
    "collection_artwork",
    "collection_member",
    "collection_tile",
    "collection_update_request",
    "generated_row",
    "group_collection_members",
    "is_series_like",
    "item_picker_view",
    "placeholder_art_for_summary",
    "placeholder_title_lines",
    "poster_from_summary",
    "poster_state",
    "progress_percent",
    "runtime_label",
    "watch_order_card",
    "watch_order_row",
    "watch_order_update_request",
)


@dataclass(frozen=True)
class ItemChildrenView:
    """Direct child cards plus the heading that best describes them."""

    title: Literal["Episodes", "Seasons"]
    children: tuple[PosterView, ...]


class LibraryPosterTransformationError(RuntimeError):
    """A poster transformation failure with only safe diagnostics attached."""

    def __init__(self, item_id: int, field_names: tuple[str, ...]) -> None:
        self.item_id = item_id
        self.field_names = field_names
        super().__init__(
            f"Library poster transformation failed for item {item_id}; "
            f"fields={','.join(field_names)}"
        )


@dataclass
class OptimisticRevisionState[ValueT]:
    """A reversible local value for mutations guarded by a collection revision."""

    value: ValueT
    _previous: ValueT | None = None

    def begin(self, replacement: ValueT) -> ValueT:
        if self._previous is not None:
            msg = "A collection mutation is already pending."
            raise RuntimeError(msg)
        self._previous = self.value
        self.value = replacement
        return self.value

    def commit(self) -> None:
        if self._previous is None:
            msg = "Cannot commit a collection mutation that is not pending."
            raise RuntimeError(msg)
        self._previous = None

    def rollback(self) -> ValueT:
        if self._previous is None:
            msg = "Cannot roll back a collection mutation that is not pending."
            raise RuntimeError(msg)
        self.value = self._previous
        self._previous = None
        return self.value


class KanvasKatalogService:
    """Transforms Katalog contracts into safe, purpose-specific Kanvas data."""

    def __init__(self, settings: Kanvas_Settings, user_id: int | None = None) -> None:
        self._settings = settings
        self._user_id = user_id

    def _required_user_id(self) -> int:
        if self._user_id is None:
            raise RuntimeError("A session profile is required for user-specific Kanvas data.")
        return self._user_id

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
                client.continue_watching(self._required_user_id(), limit=_RAIL_PAGE_SIZE),
                client.on_deck(self._required_user_id(), limit=_RAIL_PAGE_SIZE),
                client.recently_added_catalogue_items(limit=_RAIL_PAGE_SIZE),
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
                title="Recently Added",
                posters=tuple(poster_from_summary(item) for item in added_page.items),
            ),
        )

    async def administration_overview(self) -> AdministrationOverviewView:
        """Load only the small operational inputs needed by the overview."""

        async with self._client() as client:
            status, review = await gather(
                client.status(), client.metadata_review(limit=100)
            )
        return overview_from_status(
            status,
            unresolved_metadata_count=len({candidate.item_id for candidate in review.items}),
        )

    async def administration_jobs(
        self, *, cursor: str | None, limit: int = 50
    ) -> tuple[tuple[JobView, ...], str | None]:
        """Return one bounded administration job page."""

        async with self._client() as client:
            page = await client.list_jobs(cursor=cursor, limit=limit)
        return tuple(job_view(job) for job in page.items), page.next_cursor

    async def administration_roots(self) -> tuple[LibraryRootView, ...]:
        async with self._client() as client:
            roots = await client.list_library_roots()
        return tuple(library_root_view(root) for root in roots)

    async def administration_directories(self, path: str | None) -> DirectoryListing:
        async with self._client() as client:
            return await client.browse_library_directories(path)

    async def metadata_review_items(
        self, *, cursor: str | None, limit: int = 50
    ) -> tuple[tuple[MetadataReviewItemView, ...], str | None]:
        """Group the legacy candidate page by local item before rendering the workflow."""

        async with self._client() as client:
            page = await client.metadata_review(cursor=cursor, limit=limit)
            grouped: dict[int, list[MetadataReviewCandidate]] = {}
            for candidate in page.items:
                grouped.setdefault(candidate.item_id, []).append(candidate)
            local_items = await gather(*(client.get_library_item(item_id) for item_id in grouped))
        views: list[MetadataReviewItemView] = []
        for local in local_items:
            if local.item is None:
                continue
            item = local.item
            candidates = grouped[item.id]
            views.append(
                MetadataReviewItemView(
                    itemId=item.id,
                    title=item.title,
                    year=item.year,
                    kind=item.kind.value,
                    posterUrl=artwork_proxy_url(item.id, item.artwork, ArtworkKind.POSTER),
                    candidates=tuple(
                        metadata_candidate_view(candidate) for candidate in candidates
                    ),
                )
            )
        return tuple(views), page.next_cursor

    async def match_metadata_candidate(
        self, item_id: int, *, provider: str, provider_id: str
    ) -> None:
        async with self._client() as client:
            await client.match_metadata(
                item_id, MetadataMatchRequest(provider=provider, provider_id=provider_id)
            )

    async def reject_metadata_candidate(
        self, item_id: int, *, provider: str, provider_id: str
    ) -> None:
        async with self._client() as client:
            await client.reject_metadata(
                item_id, MetadataRejectRequest(provider=provider, provider_id=provider_id)
            )

    async def ignore_metadata_item(self, item_id: int) -> None:
        async with self._client() as client:
            await client.ignore_metadata(item_id)

    async def refresh_metadata_item(self, item_id: int) -> None:
        async with self._client() as client:
            await client.refresh_metadata(item_id)

    async def submit_scan(self, request: ScanRequest) -> JobView:
        async with self._client() as client:
            submission = await client.submit_scan(request)
        return job_view(submission.job)

    async def submit_library_consistency(self, request: LibraryConsistencyRequest) -> JobView:
        async with self._client() as client:
            submission = await client.submit_library_consistency(request)
        return job_view(submission.job)

    async def submit_artwork_fetch(self, request: ArtworkFetchRequest) -> JobView:
        async with self._client() as client:
            submission = await client.submit_artwork_fetch(request)
        return job_view(submission.job)

    async def hierarchy_repair_preview(self) -> HierarchyRepairPreview:
        """Load an administration-only structural proposal without raw media paths."""

        async with self._client() as client:
            return await client.hierarchy_repair_preview()

    async def submit_hierarchy_repair(self, request: HierarchyRepairRequest) -> JobView:
        async with self._client() as client:
            submission = await client.submit_hierarchy_repair(request)
        return job_view(submission.job)

    async def cancel_job(self, job_id: str) -> JobView:
        async with self._client() as client:
            return job_view(await client.cancel_job(job_id))

    async def create_library_root(self, request: LibraryRootCreate) -> LibraryRootSummary:
        async with self._client() as client:
            return await client.create_library_root(request)

    async def update_library_root(
        self, root_id: int, request: LibraryRootUpdate
    ) -> LibraryRootSummary:
        async with self._client() as client:
            return await client.update_library_root(root_id, request)

    async def delete_library_root(self, root_id: int, *, confirm: bool) -> None:
        async with self._client() as client:
            await client.delete_library_root(root_id, confirm=confirm)

    async def library_page(
        self, filters: LibraryFilters, *, cursor: str | None
    ) -> tuple[tuple[PosterView, ...], str | None]:
        """Load one deliberately bounded poster page."""

        async with self._client() as client:
            page = await client.list_library_items(
                cursor=cursor,
                limit=_GRID_PAGE_SIZE,
                kind=filters.kind,
                tags=filters.tags,
                year=filters.year,
                watched=filters.watched,
                user_id=self._required_user_id() if filters.watched is not None else None,
                availability=filters.availability,
                search=filters.search,
            )
        posters: list[PosterView] = []
        for item in page.items:
            try:
                posters.append(poster_from_summary(item))
            except Exception:
                field_names = tuple(sorted(item.model_dump().keys()))
                _LOGGER.error(
                    "Kanvas library poster transformation failed",
                    extra={"library_item_id": item.id, "library_item_fields": field_names},
                )
                raise LibraryPosterTransformationError(item.id, field_names) from None
        return tuple(posters), page.next_cursor

    async def library_tags(self) -> tuple[str, ...]:
        """Load the real tag vocabulary used by the generic library filter."""

        async with self._client() as client:
            return await client.list_library_tags()

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
            child_view = await _item_children_view(client, item.kind, children_page)
            playback = await _playback_for_item(client, self._required_user_id(), item_id)

        return ItemDetailView(
            id=item.id,
            title=item.title,
            kind=item.kind.value,
            year=item.year,
            overview=item.overview,
            posterUrl=artwork_proxy_url(item.id, item.artwork, ArtworkKind.POSTER),
            posterPlaceholder=placeholder_art_for_summary(item),
            backdropUrl=artwork_proxy_url(item.id, item.artwork, ArtworkKind.BACKDROP),
            runtimeLabel=runtime_label(media_page.items[0].duration_seconds)
            if media_page.items
            else None,
            progressPercent=progress_percent(playback),
            watched=playback.completed if playback is not None else False,
            available=item.availability is Availability.AVAILABLE,
            childSectionTitle=child_view.title,
            children=child_view.children,
        )

    async def item_edit_detail(self, item_id: int) -> LibraryItemDetail:
        """Return the full supported edit contract only to the Kanvas owner/admin UI."""

        async with self._client() as client:
            response = await client.get_library_item(item_id)
        if response.item is None:
            raise RuntimeError("Katalog returned an unexpected empty item response.")
        return response.item

    async def update_item(
        self, item_id: int, request: LibraryItemUpdate
    ) -> LibraryItemMutationResult:
        async with self._client() as client:
            return await client.update_library_item(item_id, request)

    async def item_edit_audit(self, item_id: int) -> tuple[LibraryItemEditAudit, ...]:
        async with self._client() as client:
            return await client.list_library_item_edit_audit(item_id)

    async def mark_watched(self, item_id: int) -> None:
        """Mark an item watched through Katalog's public mutation contract."""

        async with self._client() as client:
            await client.mark_watched(self._required_user_id(), item_id)

    async def clear_watched(self, item_id: int) -> None:
        """Clear watched state through Katalog's public mutation contract."""

        async with self._client() as client:
            await client.clear_watched(self._required_user_id(), item_id)

    async def artwork_content(self, item_id: int, artwork_id: int) -> tuple[bytes, str, str | None]:
        """Fetch artwork server-side so browser clients never learn Katalog's origin."""

        artwork_url = f"/api/v1/library/items/{item_id}/artwork/{artwork_id}"
        async with self._client() as client:
            artwork = await client.get_artwork_content(artwork_url)
        if artwork is None:
            msg = "Katalog returned a conditional artwork response without an entity tag."
            raise RuntimeError(msg)
        return artwork.content, artwork.content_type, artwork.etag

    async def collection_page(
        self, *, cursor: str | None, search: str | None
    ) -> tuple[tuple[CollectionTileView, ...], str | None]:
        """Load one cursor-bounded collection grid page and safe artwork cues."""

        async with self._client() as client:
            page = await client.list_collections(
                cursor=cursor, limit=_COLLECTION_GRID_PAGE_SIZE, search=search
            )
            details = await gather(*(client.get_collection(summary.id) for summary in page.items))
        return tuple(collection_tile(detail) for detail in details), page.next_cursor

    async def collection_detail(self, collection_id: int) -> CollectionDetailView:
        """Build a bounded direct-member detail view without expanding series children."""

        async with self._client() as client:
            detail, members_page, orders_page, progress_page = await gather(
                client.get_collection(collection_id),
                client.list_collection_members(collection_id, limit=_COLLECTION_MEMBER_PAGE_SIZE),
                client.list_collection_watch_orders(collection_id, limit=_WATCH_ORDER_PAGE_SIZE),
                client.continue_watching(self._required_user_id(), limit=100),
            )
            order_details = await gather(
                *(
                    client.get_watch_order(order.id, limit=_WATCH_ORDER_ENTRY_PAGE_SIZE)
                    for order in orders_page.items
                )
            )
        progress = {entry.item.id: entry.playback for entry in progress_page.items}
        members = tuple(
            collection_member(member.item, member.relationship, progress)
            for member in members_page.items
        )
        movies, series, other = group_collection_members(members)
        cards = tuple(watch_order_card(order_detail, progress) for order_detail in order_details)
        artwork_url, mosaic_urls = collection_artwork(
            detail, tuple(member.poster for member in members)
        )
        return CollectionDetailView(
            id=detail.id,
            name=detail.name,
            overview=detail.overview,
            itemCount=detail.item_count,
            watchOrderCount=detail.watch_order_count,
            revision=detail.revision,
            artworkUrl=artwork_url,
            mosaicUrls=mosaic_urls,
            movies=movies,
            series=series,
            otherMembers=other,
            memberNextCursor=members_page.next_cursor,
            watchOrders=cards,
        )

    async def watch_order_editor(self, watch_order_id: int) -> WatchOrderEditorView:
        """Load just the editor header; rows are separately cursor-paged by the browser."""

        async with self._client() as client:
            detail = await client.get_watch_order(watch_order_id, limit=1)
            collection = await client.get_collection(detail.watch_order.collection_id)
        return WatchOrderEditorView(
            id=detail.watch_order.id,
            collectionId=detail.watch_order.collection_id,
            collectionName=collection.name,
            name=detail.watch_order.name,
            kind=detail.watch_order.kind.value,
            entryCount=detail.watch_order.entry_count,
            revision=detail.watch_order.revision,
        )

    async def watch_order_page(
        self, watch_order_id: int, *, cursor: str | None
    ) -> tuple[tuple[WatchOrderRowView, ...], str | None, int]:
        """Load one bounded virtual-row page for an order editor."""

        async with self._client() as client:
            detail = await client.get_watch_order(
                watch_order_id, cursor=cursor, limit=_WATCH_ORDER_ENTRY_PAGE_SIZE
            )
        return (
            tuple(watch_order_row(entry) for entry in detail.entries.items),
            detail.entries.next_cursor,
            detail.watch_order.revision,
        )

    async def watch_order_resume_item_id(self, watch_order_id: int) -> int | None:
        """Find the first unfinished entry from Katalog's user-specific on-deck feed."""

        async with self._client() as client:
            cursor: str | None = None
            while True:
                page = await client.on_deck(
                    self._required_user_id(),
                    cursor=cursor,
                    limit=_WATCH_ORDER_ENTRY_PAGE_SIZE,
                )
                next_entry = next(
                    (
                        entry
                        for entry in page.items
                        if entry.source_watch_order_id == watch_order_id
                    ),
                    None,
                )
                if next_entry is not None:
                    return next_entry.item.id
                if page.next_cursor is None:
                    return None
                cursor = page.next_cursor

    async def item_picker_page(
        self,
        collection_id: int,
        *,
        cursor: str | None,
        search: str | None,
        playable_only: bool,
    ) -> tuple[tuple[ItemPickerView, ...], str | None]:
        """Search one server-bounded library page and mark known direct memberships."""

        async with self._client() as client:
            memberships = [
                membership
                async for membership in client.iter_collection_members(
                    collection_id, limit=_COLLECTION_MEMBER_PAGE_SIZE
                )
            ]
            page = await client.list_library_items(
                cursor=cursor, limit=_PICKER_PAGE_SIZE, search=search
            )
        member_ids = {membership.item.id for membership in memberships}
        return (
            tuple(
                item_picker_view(item, already_member=item.id in member_ids)
                for item in page.items
                if not playable_only or item.kind in PLAYABLE_KINDS
            ),
            page.next_cursor,
        )

    async def create_collection(self, *, name: str, overview: str | None) -> int:
        async with self._client() as client:
            result = await client.create_collection(CollectionCreate(name=name, overview=overview))
        return result.collection_id

    async def update_collection(
        self, collection_id: int, *, revision: int, name: str | None, overview: str | None
    ) -> int:
        async with self._client() as client:
            result = await client.update_collection(
                collection_id,
                collection_update_request(revision=revision, name=name, overview=overview),
            )
        return result.revision

    async def delete_collection(self, collection_id: int, *, revision: int) -> None:
        async with self._client() as client:
            await client.delete_collection(collection_id, expected_revision=revision)

    async def add_collection_member(
        self,
        collection_id: int,
        *,
        revision: int,
        item_id: int,
        relationship: CollectionRelationship | None,
    ) -> int:
        async with self._client() as client:
            result = await client.add_collection_member(
                collection_id,
                CollectionMembershipCreate(
                    expected_revision=revision,
                    library_item_id=item_id,
                    relationship=relationship,
                ),
            )
        return result.revision

    async def update_collection_member(
        self,
        collection_id: int,
        *,
        revision: int,
        item_id: int,
        relationship: CollectionRelationship | None,
    ) -> int:
        async with self._client() as client:
            result = await client.update_collection_member(
                collection_id,
                item_id,
                CollectionMembershipUpdate(expected_revision=revision, relationship=relationship),
            )
        return result.revision

    async def remove_collection_member(
        self, collection_id: int, *, revision: int, item_id: int
    ) -> tuple[int, tuple[str, ...]]:
        async with self._client() as client:
            result = await client.remove_collection_member(
                collection_id, item_id, expected_revision=revision
            )
        return result.revision, result.warnings

    async def create_watch_order(
        self, collection_id: int, *, collection_revision: int, name: str, kind: WatchOrderKind
    ) -> int:
        async with self._client() as client:
            result = await client.create_collection_watch_order(
                collection_id,
                WatchOrderCreate(
                    expected_collection_revision=collection_revision, name=name, kind=kind
                ),
            )
        return result.watch_order_id

    async def update_watch_order(
        self, watch_order_id: int, *, revision: int, name: str | None, kind: WatchOrderKind | None
    ) -> int:
        request = watch_order_update_request(revision=revision, name=name, kind=kind)
        async with self._client() as client:
            result = await client.update_watch_order(watch_order_id, request)
        return result.revision

    async def delete_watch_order(self, watch_order_id: int, *, revision: int) -> int:
        async with self._client() as client:
            result = await client.delete_watch_order(watch_order_id, expected_revision=revision)
        return result.collection_revision

    async def add_watch_order_entry(
        self,
        watch_order_id: int,
        *,
        revision: int,
        item_id: int,
        before_entry_id: int | None = None,
        after_entry_id: int | None = None,
    ) -> int:
        async with self._client() as client:
            result = await client.add_watch_order_entry(
                watch_order_id,
                WatchOrderEntryCreate(
                    expected_revision=revision,
                    library_item_id=item_id,
                    insert_before_entry_id=before_entry_id,
                    insert_after_entry_id=after_entry_id,
                ),
            )
        return result.revision

    async def move_watch_order_entry(
        self,
        watch_order_id: int,
        *,
        revision: int,
        entry_id: int,
        before_entry_id: int | None = None,
        after_entry_id: int | None = None,
    ) -> int:
        async with self._client() as client:
            result = await client.move_watch_order_entry(
                watch_order_id,
                entry_id,
                WatchOrderEntryMove(
                    expected_revision=revision,
                    move_before_entry_id=before_entry_id,
                    move_after_entry_id=after_entry_id,
                ),
            )
        return result.revision

    async def move_watch_order_entry_to_boundary(
        self,
        watch_order_id: int,
        *,
        revision: int,
        entry_id: int,
        boundary: Literal["start", "end"],
    ) -> int:
        """Resolve a virtual editor's absolute boundary into public move anchors."""

        async with self._client() as client:
            if boundary == "start":
                detail = await client.get_watch_order(watch_order_id, limit=1)
                first_entry = detail.entries.items[0] if detail.entries.items else None
                before_entry_id = first_entry.id if first_entry is not None else None
                after_entry_id = None
            else:
                last_entry: WatchOrderEntryDetail | None = None
                async for entry in client.iter_watch_order_entries(
                    watch_order_id, limit=_WATCH_ORDER_ENTRY_PAGE_SIZE
                ):
                    last_entry = entry
                before_entry_id = None
                after_entry_id = last_entry.id if last_entry is not None else None
            result = await client.move_watch_order_entry(
                watch_order_id,
                entry_id,
                WatchOrderEntryMove(
                    expected_revision=revision,
                    move_before_entry_id=before_entry_id,
                    move_after_entry_id=after_entry_id,
                ),
            )
        return result.revision

    async def remove_watch_order_entry(
        self, watch_order_id: int, *, revision: int, entry_id: int
    ) -> int:
        async with self._client() as client:
            result = await client.remove_watch_order_entry(
                watch_order_id, entry_id, expected_revision=revision
            )
        return result.revision

    async def generation_preview(
        self,
        watch_order_id: int,
        *,
        revision: int,
        mode: WatchOrderGenerationMode,
        apply_mode: WatchOrderGenerationApplyMode,
    ) -> GenerationPreviewView:
        request = WatchOrderGenerationRequest(
            expected_revision=revision, mode=mode, apply_mode=apply_mode
        )
        async with self._client() as client:
            preview = await client.preview_watch_order_generation(watch_order_id, request)
            current_entries: tuple[WatchOrderEntryDetail, ...] = ()
            if apply_mode is WatchOrderGenerationApplyMode.REPLACE:
                entries: list[WatchOrderEntryDetail] = []
                async for entry in client.iter_watch_order_entries(
                    watch_order_id, limit=_WATCH_ORDER_ENTRY_PAGE_SIZE
                ):
                    entries.append(entry)
                current_entries = tuple(entries)
        return GenerationPreviewView(
            watchOrderId=preview.watch_order_id,
            revision=preview.revision,
            mode=preview.mode.value,
            applyMode=apply_mode.value,
            entries=tuple(
                generated_row(item, position) for position, item in enumerate(preview.entries)
            ),
            undatedTitles=tuple(item.title for item in preview.undated_items),
            unavailableTitles=tuple(item.title for item in preview.unavailable_items),
            duplicateTitles=tuple(item.title for item in preview.duplicate_items),
            nonPlayableTitles=tuple(item.title for item in preview.non_playable_items),
            removedEntryTitles=(
                tuple(entry.item.title for entry in current_entries)
                if apply_mode is WatchOrderGenerationApplyMode.REPLACE
                else ()
            ),
        )

    async def apply_generation(
        self,
        watch_order_id: int,
        *,
        revision: int,
        mode: WatchOrderGenerationMode,
        apply_mode: WatchOrderGenerationApplyMode,
    ) -> int:
        async with self._client() as client:
            result = await client.apply_watch_order_generation(
                watch_order_id,
                WatchOrderGenerationRequest(
                    expected_revision=revision, mode=mode, apply_mode=apply_mode
                ),
            )
        return result.revision


async def _item_children_view(
    client: KatalogClient,
    item_kind: LibraryItemKind,
    children_page: PaginatedResponse[LibraryItemSummary],
) -> ItemChildrenView:
    """Flatten a single-season series so the detail page opens straight to episodes."""

    children = tuple(children_page.items)
    if (
        item_kind is LibraryItemKind.SERIES
        and len(children) == 1
        and children[0].kind is LibraryItemKind.SEASON
        and children_page.next_cursor is None
    ):
        season_page = await client.list_library_item_children(
            children[0].id, limit=_DETAIL_CHILD_PAGE_SIZE
        )
        return ItemChildrenView(
            title="Episodes",
            children=tuple(poster_from_summary(child) for child in season_page.items),
        )
    title: Literal["Episodes", "Seasons"] = (
        "Seasons"
        if item_kind is LibraryItemKind.SERIES
        and any(child.kind is LibraryItemKind.SEASON for child in children)
        else "Episodes"
    )
    return ItemChildrenView(
        title=title,
        children=tuple(poster_from_summary(child) for child in children),
    )


async def _playback_for_item(
    client: KatalogClient, user_id: int, item_id: int
) -> PlaybackStateResponse | None:
    """Return this user's saved playback state for one item, if present."""

    return await client.playback_state(user_id, item_id)

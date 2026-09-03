"""The sole Kanvas boundary around Katalog's supported public client."""

from __future__ import annotations

import logging
from asyncio import gather
from collections.abc import AsyncGenerator, Iterable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal

from kasana.kanvas.katalog_clients import katalog_client_context
from kasana.kanvas.services.presentation import (
    PLAYABLE_KINDS,
    artwork_proxy_from_api_url,
    artwork_proxy_url,
    artwork_shape_for_summary,
    collection_artwork,
    collection_member,
    collection_tile,
    collection_update_request,
    display_title,
    download_option_view,
    generated_row,
    group_collection_members,
    is_generic_episode_title,
    is_series_like,
    item_picker_view,
    placeholder_art_for_summary,
    placeholder_title_lines,
    poster_from_summary,
    poster_state,
    primary_artwork_url,
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
    WatchOrderSourceView,
    WatchOrderWorkspaceView,
)
from kasana.kanvas.viewmodels.home import HomeRailKind, MediaRailView
from kasana.kanvas.viewmodels.item import (
    CollectionChoiceView,
    ExternalLinkView,
    IncludedCollectionView,
    ItemDetailView,
)
from kasana.kanvas.viewmodels.library import (
    LibraryFilters,
    LibraryPosterPage,
    PlaceholderArtView,
    PosterState,
    PosterView,
)
from kasana.katalog.public import (
    MAX_PLAYBACK_STATE_BATCH_SIZE,
    ArtworkFetchRequest,
    ArtworkKind,
    ArtworkSelection,
    Availability,
    CollectionCreate,
    CollectionDetail,
    CollectionMembership,
    CollectionMembershipCreate,
    CollectionMembershipUpdate,
    CollectionRelationship,
    CollectionSummary,
    DirectoryListing,
    DownloadGrantRequest,
    DownloadGrantResponse,
    DuplicateEpisodeIssue,
    DuplicateResolutionBatchRequest,
    DuplicateResolutionPreview,
    DuplicateResolutionRequest,
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
    MetadataBindingReference,
    MetadataMatchRequest,
    MetadataRejectRequest,
    MetadataReviewCandidate,
    MetadataSearchResult,
    OnDeckEntry,
    PaginatedResponse,
    PlaybackStateResponse,
    PlaybackStatesRequest,
    ScanRequest,
    WatchOrderCreate,
    WatchOrderEntriesCreate,
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
_WATCH_ORDER_ENTRY_PAGE_SIZE = 100
_WATCH_ORDER_SOURCE_CHILD_PAGE_SIZE = 100
_PICKER_PAGE_SIZE = 48
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _PosterPlaybackStates:
    """Viewer-specific completion states indexed by poster item ID."""

    states_by_item_id: Mapping[int, PlaybackStateResponse]
    partially_watched_item_ids: frozenset[int]

    @classmethod
    def empty(cls) -> _PosterPlaybackStates:
        return cls(states_by_item_id={}, partially_watched_item_ids=frozenset())

    def state_for(self, item_id: int) -> PlaybackStateResponse | None:
        return self.states_by_item_id.get(item_id)

    def is_partially_watched(self, item_id: int) -> bool:
        return item_id in self.partially_watched_item_ids


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
        async with katalog_client_context(self._settings, client_factory=KatalogClient) as client:
            yield client

    async def home_rails(self) -> tuple[MediaRailView, ...]:
        """Load Home rails and the collection artwork needed by On Deck."""

        user_id = self._required_user_id()
        async with self._client() as client:
            continue_page, on_deck_page, added_page = await gather(
                client.continue_watching(user_id, limit=_RAIL_PAGE_SIZE),
                client.on_deck(user_id, limit=_RAIL_PAGE_SIZE),
                client.recently_added_catalogue_items(limit=_RAIL_PAGE_SIZE),
            )
            home_item_ids = (
                tuple(entry.item.id for entry in continue_page.items)
                + tuple(entry.item.id for entry in on_deck_page.items)
                + tuple(item.id for item in added_page.items)
            )
            on_deck_collections, playback_states = await gather(
                _on_deck_collection_details(client, on_deck_page.items),
                _poster_playback_states(client, user_id, home_item_ids),
            )

        return (
            MediaRailView(
                kind=HomeRailKind.CONTINUE,
                title="Continue",
                posters=tuple(
                    poster_from_summary(
                        entry.item,
                        playback=entry.playback,
                        partially_watched=playback_states.is_partially_watched(entry.item.id),
                        href=f"/play/item/{entry.item.id}?resume=true&onDeck=true",
                    )
                    for entry in continue_page.items
                ),
            ),
            MediaRailView(
                kind=HomeRailKind.ON_DECK,
                title="On Deck",
                posters=tuple(
                    _on_deck_poster(
                        entry,
                        source_collection=(
                            on_deck_collections.get(entry.source_collection_id)
                            if entry.source_collection_id is not None
                            else None
                        ),
                        playback_states=playback_states,
                    )
                    for entry in on_deck_page.items
                ),
            ),
            MediaRailView(
                kind=HomeRailKind.RECENTLY_ADDED,
                title="Recently Added",
                posters=tuple(
                    poster_from_summary(
                        item,
                        playback=playback_states.state_for(item.id),
                        partially_watched=playback_states.is_partially_watched(item.id),
                    )
                    for item in added_page.items
                ),
            ),
        )

    async def administration_overview(self) -> AdministrationOverviewView:
        """Load only the small operational inputs needed by the overview."""

        async with self._client() as client:
            status, review = await gather(client.status(), client.metadata_review(limit=100))
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

    async def library_grid_revision(self) -> str:
        """Return catalogue and viewer-state revisions for saved library-grid pages."""

        user_id = self._required_user_id()
        async with self._client() as client:
            roots, playback_revision = await gather(
                client.list_library_roots(),
                client.playback_state_revision(user_id),
            )
        revisions: list[str] = []
        for root in sorted(roots, key=lambda root: root.id):
            completed_at = root.last_scan_completed_at
            timestamp = completed_at.isoformat() if completed_at is not None else "never"
            revisions.append(f"{root.id}:{timestamp}")
        return f"{';'.join(revisions) or 'none'}|playback={playback_revision.revision}"

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
                    posterUrl=primary_artwork_url(item),
                    candidates=tuple(
                        metadata_candidate_view(candidate) for candidate in candidates
                    ),
                )
            )
        return tuple(views), page.next_cursor

    async def match_metadata_candidate(
        self, item_id: int, *, provider: str, provider_id: str
    ) -> None:
        await self.match_metadata(item_id, provider=provider, provider_id=provider_id)

    async def reassign_metadata_item(
        self, item_id: int, *, provider: str, provider_id: str
    ) -> None:
        """Associate an item with an administrator-selected provider record."""

        await self.match_metadata(item_id, provider=provider, provider_id=provider_id)

    async def match_metadata(self, item_id: int, *, provider: str, provider_id: str) -> None:
        """Apply one provider record through Katalog's lock-aware match operation."""

        async with self._client() as client:
            await client.match_metadata(
                item_id, MetadataMatchRequest(provider=provider, provider_id=provider_id)
            )

    async def item_metadata_binding(self, item_id: int) -> MetadataBindingReference | None:
        async with self._client() as client:
            return await client.metadata_binding(item_id)

    async def search_item_metadata(
        self, item_id: int, *, query: str
    ) -> tuple[MetadataSearchResult, ...]:
        async with self._client() as client:
            return await client.search_metadata(item_id, query=query)

    async def fetch_item_artwork(self, item_id: int) -> tuple[ArtworkSelection, ...]:
        """Fetch matching artwork choices and return the item's cached artwork."""

        async with self._client() as client:
            return await client.fetch_library_item_artwork(item_id)

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

    async def duplicate_resolution_preview(self) -> DuplicateResolutionPreview:
        """Load only current one-to-one media-less duplicate resolutions."""

        async with self._client() as client:
            return await client.duplicate_resolution_preview()

    async def duplicate_episode_issues(self) -> tuple[DuplicateEpisodeIssue, ...]:
        """Load scanner conflicts whose duplicate files need manual resolution."""

        async with self._client() as client:
            return await client.list_duplicate_episode_issues()

    async def submit_duplicate_resolution(self, request: DuplicateResolutionRequest) -> JobView:
        async with self._client() as client:
            submission = await client.submit_duplicate_resolution(request)
        return job_view(submission.job)

    async def submit_duplicate_resolution_batch(
        self, request: DuplicateResolutionBatchRequest
    ) -> JobView:
        async with self._client() as client:
            submission = await client.submit_duplicate_resolution_batch(request)
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
        self,
        filters: LibraryFilters,
        *,
        kinds: tuple[LibraryItemKind, ...],
        cursor: str | None,
    ) -> LibraryPosterPage:
        """Load one bidirectional, deliberately bounded poster page."""

        async with self._client() as client:
            page = await client.list_library_items(
                cursor=cursor,
                limit=_GRID_PAGE_SIZE,
                kinds=kinds,
                tags=filters.tags,
                year=filters.year,
                watched=filters.watched,
                user_id=self._required_user_id() if filters.watched is not None else None,
                availability=filters.availability,
                search=filters.search,
            )
            playback_states = (
                await _poster_playback_states(
                    client,
                    self._user_id,
                    (item.id for item in page.items),
                )
                if self._user_id is not None
                else _PosterPlaybackStates.empty()
            )
        posters: list[PosterView] = []
        for item in page.items:
            try:
                posters.append(
                    poster_from_summary(
                        item,
                        playback=playback_states.state_for(item.id),
                        partially_watched=playback_states.is_partially_watched(item.id),
                    )
                )
            except Exception:
                field_names = tuple(sorted(item.model_dump().keys()))
                _LOGGER.error(
                    "Kanvas library poster transformation failed",
                    extra={"library_item_id": item.id, "library_item_fields": field_names},
                )
                raise LibraryPosterTransformationError(item.id, field_names) from None
        return LibraryPosterPage(
            items=tuple(posters),
            previous_cursor=page.previous_cursor,
            next_cursor=page.next_cursor,
        )

    async def library_tags(self) -> tuple[str, ...]:
        """Load the real tag vocabulary used by the generic library filter."""

        async with self._client() as client:
            return await client.list_library_tags()

    async def item_detail(
        self, item_id: int, *, include_collection_choices: bool = False
    ) -> ItemDetailView:
        """Create a safe item view without exposing Katalog playback or media URLs."""

        async with self._client() as client:
            conditional_item = await client.get_library_item(item_id)
            if conditional_item.item is None:
                msg = "Katalog returned an unexpected empty item response."
                raise RuntimeError(msg)
            item = conditional_item.item
            if item.kind in PLAYABLE_KINDS and item.availability is Availability.AVAILABLE:
                media_page, download_options, children_page = await gather(
                    client.list_library_item_media(item_id, limit=1),
                    client.list_library_item_download_options(item_id),
                    client.list_library_item_children(item_id, limit=_DETAIL_CHILD_PAGE_SIZE),
                )
            else:
                media_page, children_page = await gather(
                    client.list_library_item_media(item_id, limit=1),
                    client.list_library_item_children(item_id, limit=_DETAIL_CHILD_PAGE_SIZE),
                )
                download_options = ()
            collection_summaries = (
                (await client.list_collections(limit=100)).items
                if include_collection_choices
                else ()
            )
            child_view = await _item_children_view(
                client, self._required_user_id(), item.kind, children_page
            )
            playback = await _playback_for_item(client, self._required_user_id(), item_id)

        return ItemDetailView(
            id=item.id,
            title=display_title(item),
            kind=item.kind.value,
            year=item.year,
            overview=item.overview,
            posterUrl=primary_artwork_url(item),
            artworkShape=artwork_shape_for_summary(item),
            posterPlaceholder=placeholder_art_for_summary(item),
            backdropUrl=artwork_proxy_url(item.id, item.artwork, ArtworkKind.BACKDROP),
            runtimeLabel=runtime_label(media_page.items[0].duration_seconds)
            if media_page.items
            else None,
            progressPercent=progress_percent(playback),
            watched=playback.completed if playback is not None else False,
            available=item.availability is Availability.AVAILABLE,
            externalLinks=_external_links(item),
            downloadOptions=tuple(download_option_view(option) for option in download_options),
            childSectionTitle=child_view.title,
            children=child_view.children,
            includedCollections=tuple(
                IncludedCollectionView(
                    id=collection.id,
                    name=collection.name,
                    revision=collection.revision,
                    relationship=(
                        collection.relationship.value
                        if collection.relationship is not None
                        else None
                    ),
                )
                for collection in item.collections
            ),
            availableCollections=_available_collection_choices(collection_summaries, item),
        )

    async def create_download_grant(
        self, item_id: int, media_file_id: int
    ) -> DownloadGrantResponse:
        """Ask Katalog for one direct-download grant owned by the active profile."""

        async with self._client() as client:
            return await client.create_download_grant(
                DownloadGrantRequest(
                    user_id=self._required_user_id(),
                    item_id=item_id,
                    media_file_id=media_file_id,
                )
            )

    async def item_edit_detail(self, item_id: int) -> LibraryItemDetail:
        """Return the full supported edit contract only to the Kanvas owner/admin UI."""

        async with self._client() as client:
            response = await client.get_library_item(item_id)
        if response.item is None:
            raise RuntimeError("Katalog returned an unexpected empty item response.")
        return response.item

    async def item_parent_choices(
        self, item_id: int, *, target_kind: LibraryItemKind
    ) -> tuple[LibraryItemSummary, ...]:
        """Return valid same-root hierarchy parents for the item editor."""

        async with self._client() as client:
            return await client.list_library_item_parent_choices(item_id, target_kind=target_kind)

    async def item_edit_collection_choices(
        self, item: LibraryItemDetail
    ) -> tuple[CollectionChoiceView, ...]:
        """Load writable collection targets for the item editor without reloading the item."""

        async with self._client() as client:
            collections = (await client.list_collections(limit=100)).items
        return _available_collection_choices(collections, item)

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

        user_id = self._required_user_id()
        async with self._client() as client:
            detail, members_page = await gather(
                client.get_collection(collection_id, user_id=user_id),
                client.list_collection_members(collection_id, limit=_COLLECTION_MEMBER_PAGE_SIZE),
            )
            playback_states = await _poster_playback_states(
                client,
                user_id,
                (member.item.id for member in members_page.items),
            )
        members = tuple(
            collection_member(
                member.item,
                member.relationship,
                playback_states.state_for(member.item.id),
                partially_watched=playback_states.is_partially_watched(member.item.id),
            )
            for member in members_page.items
        )
        movies, series, other = group_collection_members(members)
        cards = tuple(watch_order_card(order) for order in detail.watch_orders)
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
            artworkItemId=detail.artwork_item_id,
            defaultWatchOrderId=detail.default_watch_order_id,
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
            detail = await client.get_watch_order(
                watch_order_id, limit=1, user_id=self._required_user_id()
            )
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

        user_id = self._required_user_id()
        async with self._client() as client:
            detail = await client.get_watch_order(
                watch_order_id, cursor=cursor, limit=_WATCH_ORDER_ENTRY_PAGE_SIZE
            )
            playback_states = await _poster_playback_states(
                client,
                user_id,
                (entry.item.id for entry in detail.entries.items),
            )
        return (
            tuple(
                watch_order_row(
                    entry,
                    playback=playback_states.state_for(entry.item.id),
                    partially_watched=playback_states.is_partially_watched(entry.item.id),
                )
                for entry in detail.entries.items
            ),
            detail.entries.next_cursor,
            detail.watch_order.revision,
        )

    async def watch_order_workspace(self, watch_order_id: int) -> WatchOrderWorkspaceView:
        """Load one order and all collection-backed sources eligible to extend it."""

        user_id = self._required_user_id()
        async with self._client() as client:
            detail = await client.get_watch_order(watch_order_id, limit=1, user_id=user_id)
            entry_list: list[WatchOrderEntryDetail] = []
            async for entry in client.iter_watch_order_entries(
                watch_order_id, limit=_WATCH_ORDER_ENTRY_PAGE_SIZE
            ):
                entry_list.append(entry)
            existing_entries = tuple(entry_list)
            entry_playback_states, sources = await gather(
                _poster_playback_states(
                    client,
                    user_id,
                    (entry.item.id for entry in existing_entries),
                ),
                self._watch_order_sources(
                    client,
                    detail.watch_order.collection_id,
                    include_playback=True,
                ),
            )
        existing_item_ids = frozenset(entry.item.id for entry in existing_entries)
        available_sources = tuple(
            source for source, item_ids in sources if not existing_item_ids.intersection(item_ids)
        )
        return WatchOrderWorkspaceView(
            revision=detail.watch_order.revision,
            entries=tuple(
                watch_order_row(
                    entry,
                    playback=entry_playback_states.state_for(entry.item.id),
                    partially_watched=entry_playback_states.is_partially_watched(entry.item.id),
                )
                for entry in existing_entries
            ),
            sources=available_sources,
        )

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
        self,
        collection_id: int,
        *,
        revision: int,
        name: str | None,
        overview: str | None,
        artwork_item_id: int | None = None,
        default_watch_order_id: int | None = None,
        update_preferences: bool = False,
    ) -> int:
        async with self._client() as client:
            result = await client.update_collection(
                collection_id,
                collection_update_request(
                    revision=revision,
                    name=name,
                    overview=overview,
                    artwork_item_id=artwork_item_id,
                    default_watch_order_id=default_watch_order_id,
                    update_preferences=update_preferences,
                ),
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

    async def add_watch_order_source(
        self,
        watch_order_id: int,
        *,
        revision: int,
        source_item_id: int,
        before_entry_id: int | None = None,
    ) -> int:
        """Add a collection item, expanding non-playable descendants into one ordered block."""

        return await self.add_watch_order_sources(
            watch_order_id,
            revision=revision,
            source_item_ids=(source_item_id,),
            before_entry_id=before_entry_id,
        )

    async def add_watch_order_sources(
        self,
        watch_order_id: int,
        *,
        revision: int,
        source_item_ids: tuple[int, ...],
        before_entry_id: int | None = None,
    ) -> int:
        """Insert selected collection sources as one contiguous, ordered watch-order block."""

        if not source_item_ids:
            raise ValueError("Select at least one collection item.")
        if len(set(source_item_ids)) != len(source_item_ids):
            raise ValueError("Selected collection items must be unique.")

        async with self._client() as client:
            detail = await client.get_watch_order(watch_order_id, limit=1)
            sources = await self._watch_order_sources(client, detail.watch_order.collection_id)
            source_item_ids_by_id = {source.id: item_ids for source, item_ids in sources}
            target_ids: list[int] = []
            selected_item_ids: set[int] = set()
            for source_item_id in source_item_ids:
                source_ids = source_item_ids_by_id.get(source_item_id)
                if source_ids is None:
                    raise ValueError("A selected item is not available from this collection.")
                if not source_ids:
                    raise ValueError("A selected item has no playable descendants.")
                if selected_item_ids.intersection(source_ids):
                    raise ValueError("Selected items overlap in their playable entries.")
                selected_item_ids.update(source_ids)
                target_ids.extend(source_ids)
            result = await client.add_watch_order_entries(
                watch_order_id,
                WatchOrderEntriesCreate(
                    expected_revision=revision,
                    library_item_ids=tuple(target_ids),
                    insert_before_entry_id=before_entry_id,
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

    async def _watch_order_sources(
        self,
        client: KatalogClient,
        collection_id: int,
        *,
        include_playback: bool = False,
    ) -> tuple[tuple[WatchOrderSourceView, tuple[int, ...]], ...]:
        """Expose direct members and every recursive child as one source card each."""

        membership_list: list[CollectionMembership] = []
        async for membership in client.iter_collection_members(
            collection_id, limit=_COLLECTION_MEMBER_PAGE_SIZE
        ):
            membership_list.append(membership)
        memberships = tuple(membership_list)
        children_by_parent: dict[int, tuple[LibraryItemSummary, ...]] = {}

        async def children(item_id: int) -> tuple[LibraryItemSummary, ...]:
            cached = children_by_parent.get(item_id)
            if cached is not None:
                return cached
            loaded = tuple(
                sorted(
                    await self._library_children(client, item_id),
                    key=_watch_order_child_sort_key,
                )
            )
            children_by_parent[item_id] = loaded
            return loaded

        async def source_tree(item: LibraryItemSummary) -> tuple[LibraryItemSummary, ...]:
            descendants: list[LibraryItemSummary] = [item]
            for child in await children(item.id):
                descendants.extend(await source_tree(child))
            return tuple(descendants)

        async def source_items(item: LibraryItemSummary) -> tuple[LibraryItemSummary, ...]:
            if item.kind in PLAYABLE_KINDS:
                return (item,)
            targets: list[LibraryItemSummary] = []
            for child in await children(item.id):
                targets.extend(await source_items(child))
            return tuple(targets)

        candidates: dict[int, LibraryItemSummary] = {}
        for membership in memberships:
            for item in await source_tree(membership.item):
                candidates.setdefault(item.id, item)
        playback_states = (
            await _poster_playback_states(
                client,
                self._required_user_id(),
                candidates,
            )
            if include_playback
            else _PosterPlaybackStates.empty()
        )
        sources: list[tuple[WatchOrderSourceView, tuple[int, ...]]] = []
        for item in candidates.values():
            target_items = await source_items(item)
            item_ids = tuple(target.id for target in target_items)
            available = (
                all(target.availability is Availability.AVAILABLE for target in target_items)
                if target_items
                else item.availability is Availability.AVAILABLE
            )
            sources.append(
                (
                    WatchOrderSourceView(
                        id=item.id,
                        title=item.title,
                        kind=item.kind.value,
                        year=item.year,
                        seriesTitle=item.series_title,
                        seasonNumber=item.season_number,
                        entryCount=len(item_ids),
                        addable=bool(item_ids),
                        available=available,
                        poster=poster_from_summary(
                            item,
                            playback=playback_states.state_for(item.id),
                            partially_watched=playback_states.is_partially_watched(item.id),
                            detail=_watch_order_source_subtitle(
                                item, len(item_ids), bool(item_ids)
                            ),
                        ),
                    ),
                    item_ids,
                )
            )
        return tuple(sources)

    async def _library_children(
        self, client: KatalogClient, item_id: int
    ) -> tuple[LibraryItemSummary, ...]:
        children: list[LibraryItemSummary] = []
        cursor: str | None = None
        while True:
            page = await client.list_library_item_children(
                item_id, cursor=cursor, limit=_WATCH_ORDER_SOURCE_CHILD_PAGE_SIZE
            )
            children.extend(page.items)
            if page.next_cursor is None:
                return tuple(children)
            cursor = page.next_cursor


async def _poster_playback_states(
    client: KatalogClient,
    user_id: int,
    item_ids: Iterable[int],
) -> _PosterPlaybackStates:
    """Load direct and aggregate completion state in bounded Katalog batches."""

    requested_item_ids = tuple(sorted(set(item_ids)))
    if not requested_item_ids:
        return _PosterPlaybackStates.empty()
    states_by_item_id: dict[int, PlaybackStateResponse] = {}
    partially_watched_item_ids: set[int] = set()
    for start in range(0, len(requested_item_ids), MAX_PLAYBACK_STATE_BATCH_SIZE):
        response = await client.playback_states(
            user_id,
            PlaybackStatesRequest(
                item_ids=requested_item_ids[start : start + MAX_PLAYBACK_STATE_BATCH_SIZE]
            ),
        )
        states_by_item_id.update({state.item_id: state for state in response.states})
        partially_watched_item_ids.update(response.partially_watched_item_ids)
    return _PosterPlaybackStates(
        states_by_item_id=states_by_item_id,
        partially_watched_item_ids=frozenset(partially_watched_item_ids),
    )


def _external_links(item: LibraryItemDetail) -> tuple[ExternalLinkView, ...]:
    """Expose only identifiers with a well-defined, safe public destination."""

    links: list[ExternalLinkView] = []
    for identifier in item.external_ids:
        if identifier.namespace.casefold() != "imdb":
            continue
        title_id = identifier.value.casefold()
        if not _is_imdb_title_id(title_id):
            continue
        links.append(ExternalLinkView(label="IMDb", url=f"https://www.imdb.com/title/{title_id}/"))
    return tuple(links)


def _is_imdb_title_id(value: str) -> bool:
    return value.startswith("tt") and value[2:].isdigit() and 7 <= len(value[2:]) <= 10


def _available_collection_choices(
    collections: tuple[CollectionSummary, ...], item: LibraryItemDetail
) -> tuple[CollectionChoiceView, ...]:
    included_ids = frozenset(collection.id for collection in item.collections)
    return tuple(
        CollectionChoiceView(id=collection.id, name=collection.name, revision=collection.revision)
        for collection in collections
        if collection.id not in included_ids
    )


async def _item_children_view(
    client: KatalogClient,
    user_id: int,
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
            children=await _child_posters(client, user_id, season_page.items),
        )
    title: Literal["Episodes", "Seasons"] = (
        "Seasons"
        if item_kind is LibraryItemKind.SERIES
        and any(child.kind is LibraryItemKind.SEASON for child in children)
        else "Episodes"
    )
    return ItemChildrenView(
        title=title,
        children=await _child_posters(client, user_id, children),
    )


async def _child_posters(
    client: KatalogClient, user_id: int, children: tuple[LibraryItemSummary, ...]
) -> tuple[PosterView, ...]:
    """Render children with each viewer's saved watched state."""

    child_ids = tuple(child.id for child in children)
    playback_states = (
        await client.playback_states(user_id, PlaybackStatesRequest(item_ids=child_ids))
        if child_ids
        else None
    )
    playback_by_item_id = (
        {state.item_id: state for state in playback_states.states}
        if playback_states is not None
        else {}
    )
    partially_watched_item_ids: set[int] = (
        {item_id for item_id in playback_states.partially_watched_item_ids}
        if playback_states is not None
        else set()
    )
    return tuple(
        poster_from_summary(
            child,
            playback=playback_by_item_id.get(child.id),
            partially_watched=child.id in partially_watched_item_ids,
        )
        for child in children
    )


def _watch_order_source_subtitle(item: LibraryItemSummary, entry_count: int, addable: bool) -> str:
    """Describe a collection-browser card and its recursive playable block."""

    labels = [item.series_title] if item.series_title else []
    labels.append(item.kind.value.replace("_", " ").title())
    if item.season_number is not None:
        labels.append(f"Season {item.season_number}")
    if addable:
        labels.append("1 entry" if entry_count == 1 else f"{entry_count} episodes")
    else:
        labels.append("No playable descendants")
    return " · ".join(labels)


def _watch_order_child_sort_key(item: LibraryItemSummary) -> tuple[bool, int, bool, int, str, int]:
    """Keep a dragged show or season in its natural season and episode sequence."""

    return (
        item.season_number is None,
        item.season_number if item.season_number is not None else 0,
        item.episode_number is None,
        item.episode_number if item.episode_number is not None else 0,
        item.title.casefold(),
        item.id,
    )


async def _playback_for_item(
    client: KatalogClient, user_id: int, item_id: int
) -> PlaybackStateResponse | None:
    """Return this user's saved playback state for one item, if present."""

    return await client.playback_state(user_id, item_id)


async def _on_deck_collection_details(
    client: KatalogClient, entries: tuple[OnDeckEntry, ...]
) -> dict[int, CollectionDetail]:
    """Load each collection backing On Deck once so its identity art can be shown."""

    collection_ids = tuple(
        sorted(
            {
                entry.source_collection_id
                for entry in entries
                if entry.source_collection_id is not None
            }
        )
    )
    if not collection_ids:
        return {}
    details = await gather(
        *(client.get_collection(collection_id) for collection_id in collection_ids)
    )
    return {detail.id: detail for detail in details}


def _on_deck_poster(
    entry: OnDeckEntry,
    *,
    source_collection: CollectionDetail | None,
    playback_states: _PosterPlaybackStates,
) -> PosterView:
    """Show each queue source as a series or collection with its next launch target."""

    if entry.source_watch_order_id is None:
        return poster_from_summary(
            entry.item,
            playback=playback_states.state_for(entry.item.id),
            partially_watched=(
                entry.partially_watched or playback_states.is_partially_watched(entry.item.id)
            ),
            href=f"/play/item/{entry.item.id}?resume=true&onDeck=true",
            detail=_on_deck_item_detail(entry.next_item),
        )
    if entry.source_collection_id is None:
        raise RuntimeError("A collection-backed On Deck entry requires its collection ID.")
    if source_collection is None:
        raise RuntimeError("On Deck collection artwork could not be loaded.")
    if source_collection.id != entry.source_collection_id:
        raise RuntimeError("On Deck collection artwork did not match its queue source.")
    collection_name = entry.source_collection_name or source_collection.name
    artwork_url, mosaic_urls = collection_artwork(
        source_collection,
        tuple(poster_from_summary(member.item) for member in source_collection.members),
    )
    return PosterView(
        id=entry.source_collection_id,
        title=collection_name,
        detail=_on_deck_item_detail(entry.next_item or entry.item),
        href=f"/play/watch-orders/{entry.source_watch_order_id}?resume=true&onDeck=true",
        posterUrl=artwork_url,
        mosaicUrls=mosaic_urls,
        placeholder=PlaceholderArtView(lines=(collection_name,)),
        state=PosterState.NORMAL,
        partiallyWatched=entry.partially_watched,
        available=True,
    )


def _on_deck_item_detail(item: LibraryItemSummary | None) -> str:
    """Format the exact queued item's compact episode context and title."""

    if item is None:
        return "Next episode"
    title = display_title(item)
    if item.kind is LibraryItemKind.EPISODE and is_generic_episode_title(
        title, item.episode_number, item.series_title
    ):
        title = None
    return " · ".join(part for part in (item.context_label, title) if part) or "Next episode"

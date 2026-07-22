"""Behaviour contracts for the first Kanvas visual foundation."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from _pytest.monkeypatch import MonkeyPatch
from fastapi import HTTPException
from nicegui import app
from nicegui.client import Client
from nicegui.element import Element
from nicegui.page import page
from pydantic import TypeAdapter
from starlette.datastructures import FormData
from starlette.requests import Request
from starlette.routing import Route

from kasana.kanvas import __main__ as kanvas_main
from kasana.kanvas import dashboard
from kasana.kanvas.components.browser import BrowserComponent, mount_browser_component
from kasana.kanvas.components.collections import (
    collection_artwork as render_collection_artwork,
)
from kasana.kanvas.components.collections import (
    generation_preview,
)
from kasana.kanvas.components.controls import NavigationAction, keyboard_action
from kasana.kanvas.components.feedback import feedback_state, skeleton_posters
from kasana.kanvas.components.inputs import textarea_input
from kasana.kanvas.components.media_rail import media_rail
from kasana.kanvas.components.navigation import primary_navigation
from kasana.kanvas.components.poster import poster_card
from kasana.kanvas.components.progress import progress_indicator
from kasana.kanvas.components.shell import (
    kanvas_asset_versions,
    kanvas_head_html,
    page_shell,
)
from kasana.kanvas.dashboard import (
    administration_action,
    administration_artwork_page,
    administration_directories_data,
    administration_jobs_data,
    administration_jobs_page,
    administration_libraries_page,
    administration_metadata_data,
    administration_metadata_page,
    administration_overview_data,
    administration_page,
    administration_roots_data,
    apply_watch_order_generation_action,
    artwork,
    build_dashboard,
    collection_member_action,
    collection_picker_data,
    collections_data,
    collections_page,
    create_collection_action,
    create_watch_order_action,
    delete_collection_action,
    delete_watch_order_action,
    design_page,
    item_edit_action,
    item_edit_data,
    library_data,
    remove_collection_member_action,
    update_collection_action,
    update_collection_member_action,
    update_watch_order_action,
    watch_order_data,
    watch_order_entry_action,
    watch_order_launch_action,
)
from kasana.kanvas.profiles import SessionProfile
from kasana.kanvas.routes import collections as collections_route
from kasana.kanvas.routes import home as home_route
from kasana.kanvas.routes import item as item_route
from kasana.kanvas.routes import library as library_route
from kasana.kanvas.routes.administration import render_administration
from kasana.kanvas.routes.library import render_library
from kasana.kanvas.services.katalog import (
    KanvasKatalogService,
    LibraryPosterTransformationError,
    OptimisticRevisionState,
    collection_artwork,
    group_collection_members,
    poster_from_summary,
    poster_state,
)
from kasana.kanvas.services.playback import (
    OptimisticWatchedState,
    launch_uri,
    playback_plan_request,
    watch_order_playback_plan_request,
)
from kasana.kanvas.settings import Kanvas_Settings
from kasana.kanvas.viewmodels.administration import (
    AdaptivePollingState,
    AdministrationOverviewView,
    JobView,
    LibraryRootView,
    MetadataCandidateView,
    MetadataReviewItemView,
    job_view,
    overview_from_status,
)
from kasana.kanvas.viewmodels.collections import (
    CollectionDetailView,
    CollectionMemberView,
    CollectionTileView,
    GenerationPreviewView,
    ItemPickerView,
    WatchOrderCardView,
    WatchOrderEditorView,
    WatchOrderRowView,
)
from kasana.kanvas.viewmodels.home import MediaRailView
from kasana.kanvas.viewmodels.item import ItemDetailView
from kasana.kanvas.viewmodels.library import (
    CursorPager,
    LibraryFilters,
    LibraryPageEnvelope,
    PosterState,
    PosterView,
)
from kasana.katalog.public import (
    ArtworkFetchRequest,
    ArtworkKind,
    ArtworkSelection,
    Availability,
    BackgroundJob,
    CollectionDetail,
    CollectionMembership,
    DirectoryEntry,
    DirectoryListing,
    JobProgress,
    JobStatus,
    KatalogClientError,
    KatalogClientErrorKind,
    LibraryConsistencyRequest,
    LibraryItemDetail,
    LibraryItemEditAudit,
    LibraryItemKind,
    LibraryItemMutationResult,
    LibraryItemSummary,
    LibraryItemUpdate,
    LibraryRootCreate,
    LibraryRootKind,
    LibraryRootSummary,
    LibraryRootUpdate,
    MetadataReviewCandidate,
    PaginatedResponse,
    PlaybackStateResponse,
    ScanRequest,
    SelectedArtwork,
    SeriesPlaybackContext,
    StandalonePlaybackContext,
    StatusResponse,
    UserRole,
    UserSummary,
    WatchedFilter,
    WatchOrderPlaybackContext,
)

_EDITABLE_ITEM_ADAPTER: TypeAdapter[LibraryItemDetail] = TypeAdapter(LibraryItemDetail)


@pytest.fixture(autouse=True)
def active_profile(monkeypatch: MonkeyPatch) -> SessionProfile:
    """Give legacy route-contract tests a selected owner profile."""

    profile = SessionProfile(UserSummary(id=1, username="tester", role=UserRole.OWNER))

    async def current_profile(_request: object) -> SessionProfile:
        return profile

    monkeypatch.setattr(dashboard, "_data_profile", current_profile)
    return profile


def _selected_profile() -> SessionProfile:
    return SessionProfile(UserSummary(id=1, username="tester", role=UserRole.OWNER))


def _item(*, artwork: tuple[ArtworkSelection, ...] = ()) -> LibraryItemSummary:
    return LibraryItemSummary(
        id=7,
        title="A title",
        kind=LibraryItemKind.MOVIE,
        year=2004,
        availability=Availability.AVAILABLE,
        artwork=artwork,
    )


def _editable_item(*, title: str = "A title") -> LibraryItemDetail:
    """Build the public item-detail union exactly as Katalog returns it."""

    return _EDITABLE_ITEM_ADAPTER.validate_python(
        {
            "id": 7,
            "title": title,
            "sort_title": title,
            "kind": LibraryItemKind.MOVIE,
            "year": 2004,
            "availability": Availability.AVAILABLE,
            "tags": ["anime"],
            "artwork": [
                {
                    "id": 8,
                    "kind": ArtworkKind.POSTER,
                    "url": "/api/v1/library/items/7/artwork/8",
                    "content_type": "image/jpeg",
                    "size_bytes": 4,
                }
            ],
            "overview": "An overview",
            "release_date": "2004-02-03",
            "locked_metadata_fields": ["title"],
            "selected_artwork": [{"kind": "poster", "artwork_id": 8}],
            "playback_url": "/api/v1/playback/items/7",
        }
    )


def _playback(*, completed: bool = False) -> PlaybackStateResponse:
    return PlaybackStateResponse(
        user_id=1,
        item_id=7,
        position_seconds=25,
        duration_seconds=100,
        completed=completed,
        play_count=0,
        last_played_at=datetime.now(UTC),
    )


def _admin_job() -> JobView:
    return JobView(
        id="job-1",
        kind="scan",
        status="running",
        rootId=1,
        phase="scanning",
        progressCurrent=2,
        progressTotal=4,
        progressUnit="files",
        submittedAt=datetime.now(UTC),
        cancellable=True,
        cancellationRequested=False,
    )


def test_job_view_exposes_failure_reason_for_failed_rows() -> None:
    job = BackgroundJob(
        id="job-1",
        kind="scan",
        status=JobStatus.FAILED,
        submitted_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        progress=JobProgress(phase="matching", current=358, total=358, unit="files"),
        message="Maintenance job failed.",
        failure_code="runtimeerror",
        failure_message="TMDB request failed.",
        cancellable=False,
    )

    view = job_view(job)
    payload = view.model_dump(by_alias=True, mode="json")

    assert payload["status"] == "failed"
    assert payload["message"] == "Maintenance job failed."
    assert payload["failure"] == "TMDB request failed."


def test_poster_view_transformation_is_safe_and_expresses_progress() -> None:
    artwork = ArtworkSelection(
        id=8,
        kind=ArtworkKind.POSTER,
        url="/api/v1/library/items/7/artwork/8",
        content_type="image/jpeg",
        size_bytes=4,
    )

    poster = poster_from_summary(_item(artwork=(artwork,)), playback=_playback())

    assert poster.poster_url == "/kanvas/artwork/7/8"
    assert poster.progress_percent == 25
    assert poster.state is PosterState.IN_PROGRESS
    assert "playback_url" not in json.dumps(poster.model_dump(mode="json"))
    assert "/tmp/" not in json.dumps(poster.model_dump(mode="json"))


def test_poster_state_precedence_covers_missing_and_unavailable_artwork() -> None:
    assert (
        poster_state(
            available=False, has_artwork=True, playback=None, selected=False, loading=False
        )
        is PosterState.UNAVAILABLE
    )
    assert (
        poster_state(
            available=True, has_artwork=False, playback=None, selected=False, loading=False
        )
        is PosterState.MISSING_ARTWORK
    )
    assert (
        poster_state(
            available=True,
            has_artwork=True,
            playback=_playback(completed=True),
            selected=False,
            loading=False,
        )
        is PosterState.WATCHED
    )


def test_filter_mapping_and_cursor_pagination_prevent_duplicate_requests() -> None:
    filters = LibraryFilters.from_query(
        {
            "search": "  Ghost  ",
            "kind": "movie",
            "watched": "in_progress",
            "availability": "available",
            "year": "2001",
        },
        tags=("anime", "favourite"),
    )

    assert filters.to_katalog_arguments() == {
        "kind": LibraryItemKind.MOVIE,
        "tags": ("anime", "favourite"),
        "year": 2001,
        "watched": WatchedFilter.IN_PROGRESS,
        "availability": Availability.AVAILABLE,
        "search": "Ghost",
    }
    pager = CursorPager()
    assert pager.begin_request() is None
    assert pager.begin_request() is None
    pager.fail_request()
    assert pager.begin_request() is None
    pager.complete_request("next")
    assert pager.begin_request() == "next"
    pager.complete_request(None)
    assert pager.begin_request() is None


def test_playback_plan_request_and_one_use_uri_do_not_contain_media_locations() -> None:
    movie = cast(
        LibraryItemDetail,
        SimpleNamespace(id=7, kind=LibraryItemKind.MOVIE, parent_id=None),
    )
    series = cast(
        LibraryItemDetail,
        SimpleNamespace(id=9, kind=LibraryItemKind.SERIES, parent_id=None),
    )

    movie_request = playback_plan_request(movie, user_id=1, resume=False)
    series_request = playback_plan_request(series, user_id=1, resume=True)
    uri = launch_uri("A" * 32)

    assert isinstance(movie_request.context, StandalonePlaybackContext)
    assert isinstance(series_request.context, SeriesPlaybackContext)
    assert series_request.context.resume is True
    assert uri == f"kasana://play/{'A' * 32}"
    assert "/api/v1/media/" not in uri


def test_watch_order_playback_plan_preserves_the_order_context_for_play_from_here() -> None:
    request = watch_order_playback_plan_request(17, user_id=3, start_item_id=9)

    assert isinstance(request.context, WatchOrderPlaybackContext)
    assert request.context.watch_order_id == 17
    assert request.context.start_item_id == 9


def test_collection_mosaic_is_stable_and_never_returns_an_absolute_artwork_path() -> None:
    detail = CollectionDetail(
        id=1,
        name="Stargate",
        item_count=3,
        watch_order_count=0,
        revision=1,
    )
    posters = (
        PosterView(
            id=1,
            title="First",
            href="/item/1",
            posterUrl="/kanvas/artwork/1/11",
            available=True,
        ),
        PosterView(
            id=2,
            title="Missing",
            href="/item/2",
            posterUrl="/kanvas/artwork/2/12",
            available=False,
        ),
        PosterView(
            id=3,
            title="Second",
            href="/item/3",
            posterUrl="/kanvas/artwork/3/13",
            available=True,
        ),
    )

    artwork, mosaic = collection_artwork(detail, posters)

    assert artwork is None
    assert mosaic == ("/kanvas/artwork/1/11", "/kanvas/artwork/3/13")
    assert all(value.startswith("/kanvas/") for value in mosaic)


def test_collection_grouping_keeps_mixed_direct_members_without_expanding_series() -> None:
    def member(item_id: int, kind: str) -> CollectionMemberView:
        return CollectionMemberView(
            poster=PosterView(id=item_id, title=kind, href=f"/item/{item_id}", available=True),
            kind=kind,
        )

    movies, series, other = group_collection_members(
        (member(1, "movie"), member(2, "series"), member(3, "episode"))
    )

    assert [entry.poster.id for entry in movies] == [1]
    assert [entry.poster.id for entry in series] == [2]
    assert [entry.poster.id for entry in other] == [3]


def test_collection_optimistic_state_can_rollback_a_safe_mutation() -> None:
    state: OptimisticRevisionState[tuple[int, int]] = OptimisticRevisionState(value=(1, 2))

    assert state.begin((2, 1)) == (2, 1)
    assert state.rollback() == (1, 2)
    assert state.value == (1, 2)


def test_watched_state_can_commit_or_rollback_an_optimistic_update() -> None:
    state = OptimisticWatchedState(watched=False)

    assert state.toggle() is True
    assert state.rollback() is False
    assert state.toggle() is True
    state.commit()
    assert state.watched is True
    with pytest.raises(RuntimeError, match="not pending"):
        state.rollback()


async def test_library_endpoint_exposes_intentional_katalog_failure_state(
    monkeypatch: MonkeyPatch,
) -> None:
    async def unavailable(
        _self: object, _filters: LibraryFilters, *, cursor: str | None
    ) -> tuple[tuple[PosterView, ...], str | None]:
        raise KatalogClientError(KatalogClientErrorKind.UNAVAILABLE, "offline")

    monkeypatch.setattr("kasana.kanvas.dashboard.KanvasKatalogService.library_page", unavailable)
    request = Request({"type": "http", "query_string": b"search=ghost", "headers": []})

    response = await library_data(request)

    assert response.status_code == 503
    payload = json.loads(bytes(response.body))
    assert payload["error"]["code"] == "library_unavailable"
    assert payload["error"]["message"] == "Katalog could not load the library."
    assert response.headers["x-request-id"] == payload["error"]["requestId"]


async def test_library_endpoint_serialises_only_safe_poster_data(monkeypatch: MonkeyPatch) -> None:
    async def page(
        _self: object, _filters: LibraryFilters, *, cursor: str | None
    ) -> tuple[tuple[PosterView, ...], str | None]:
        assert cursor == "later"
        return (
            (
                PosterView(
                    id=7,
                    title="Safe",
                    href="/item/7",
                    posterUrl="/kanvas/artwork/7/8",
                    available=True,
                ),
            ),
            None,
        )

    monkeypatch.setattr("kasana.kanvas.dashboard.KanvasKatalogService.library_page", page)
    request = Request(
        {
            "type": "http",
            "query_string": b"cursor=later",
            "headers": [(b"x-request-id", b"library-request-7")],
        }
    )

    response = await library_data(request)

    assert response.status_code == 200
    payload = json.loads(bytes(response.body))
    assert payload["schemaVersion"] == 1
    assert payload["requestId"] == "library-request-7"
    assert response.headers["x-request-id"] == "library-request-7"
    assert payload["nextCursor"] is None
    assert payload["items"][0]["posterUrl"] == "/kanvas/artwork/7/8"
    assert "playback_url" not in json.dumps(payload)


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (KatalogClientError(KatalogClientErrorKind.VALIDATION, "bad Katalog request"), 422),
        (
            KatalogClientError(KatalogClientErrorKind.RESPONSE, "Katalog failed", status_code=500),
            502,
        ),
        (KatalogClientError(KatalogClientErrorKind.UNAVAILABLE, "Katalog offline"), 503),
    ],
)
async def test_library_endpoint_preserves_typed_katalog_failure_statuses(
    monkeypatch: MonkeyPatch,
    error: KatalogClientError,
    expected_status: int,
) -> None:
    async def failed(
        _self: object, _filters: LibraryFilters, *, cursor: str | None
    ) -> tuple[tuple[PosterView, ...], str | None]:
        raise error

    monkeypatch.setattr("kasana.kanvas.dashboard.KanvasKatalogService.library_page", failed)

    response = await library_data(
        Request(
            {
                "type": "http",
                "query_string": b"",
                "headers": [(b"x-request-id", b"typed-katalog-failure")],
            }
        )
    )

    assert response.status_code == expected_status
    assert response.headers["x-request-id"] == "typed-katalog-failure"
    assert json.loads(bytes(response.body)) == {
        "error": {
            "code": "library_unavailable",
            "message": "Katalog could not load the library.",
            "requestId": "typed-katalog-failure",
        }
    }


async def test_library_endpoint_hides_unexpected_and_serialisation_failures(
    monkeypatch: MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    leaking_value = "/media/private/film.mkv?access_token=secret"

    async def broken(
        _self: object, _filters: LibraryFilters, *, cursor: str | None
    ) -> tuple[tuple[PosterView, ...], str | None]:
        raise RuntimeError(leaking_value)

    monkeypatch.setattr("kasana.kanvas.dashboard.KanvasKatalogService.library_page", broken)
    response = await library_data(Request({"type": "http", "query_string": b"", "headers": []}))

    assert response.status_code == 500
    assert json.loads(bytes(response.body))["error"] == {
        "code": "library_unavailable",
        "message": "Katalog could not load the library.",
        "requestId": response.headers["x-request-id"],
    }
    assert leaking_value not in caplog.text

    async def page(
        _self: object, _filters: LibraryFilters, *, cursor: str | None
    ) -> tuple[tuple[PosterView, ...], str | None]:
        return (PosterView(id=7, title="Safe", href="/item/7", available=True),), None

    def serialisation_failure(
        _self: LibraryPageEnvelope, **_arguments: object
    ) -> dict[str, object]:
        raise TypeError("serialisation failed")

    monkeypatch.setattr("kasana.kanvas.dashboard.KanvasKatalogService.library_page", page)
    monkeypatch.setattr(LibraryPageEnvelope, "model_dump", serialisation_failure)
    serialisation = await library_data(
        Request({"type": "http", "query_string": b"", "headers": []})
    )

    assert serialisation.status_code == 500
    assert json.loads(bytes(serialisation.body))["error"]["code"] == "library_unavailable"


async def test_library_endpoint_development_diagnostic_is_safe_and_opt_in(
    monkeypatch: MonkeyPatch,
) -> None:
    async def transformation_failure(
        _self: object, _filters: LibraryFilters, *, cursor: str | None
    ) -> tuple[tuple[PosterView, ...], str | None]:
        raise LibraryPosterTransformationError(7, ("artwork", "id", "title"))

    monkeypatch.setattr(
        "kasana.kanvas.dashboard.KanvasKatalogService.library_page", transformation_failure
    )
    monkeypatch.setattr(dashboard, "_settings", Kanvas_Settings(development_mode=True))
    response = await library_data(Request({"type": "http", "query_string": b"", "headers": []}))

    assert response.status_code == 500
    assert json.loads(bytes(response.body))["error"]["diagnostic"] == "poster_transformation"


async def test_library_poster_transformation_logs_only_safe_item_diagnostics(
    monkeypatch: MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    item = _item()
    leaking_value = "/media/private/film.mkv?access_token=secret"

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_arguments: object) -> None:
            pass

        async def list_library_items(
            self, **_arguments: object
        ) -> PaginatedResponse[LibraryItemSummary]:
            return PaginatedResponse(items=(item,), next_cursor=None, limit=48)

    def fake_client(*_arguments: object, **_keyword_arguments: object) -> FakeClient:
        return FakeClient()

    def transformation_failure(_item: LibraryItemSummary) -> PosterView:
        raise RuntimeError(leaking_value)

    monkeypatch.setattr("kasana.kanvas.services.katalog.KatalogClient", fake_client)
    monkeypatch.setattr(
        "kasana.kanvas.services.katalog.poster_from_summary", transformation_failure
    )

    with pytest.raises(LibraryPosterTransformationError) as error:
        await KanvasKatalogService(Kanvas_Settings()).library_page(LibraryFilters(), cursor=None)

    assert error.value.item_id == 7
    assert error.value.field_names == (
        "artwork",
        "availability",
        "id",
        "kind",
        "parent_id",
        "tags",
        "title",
        "year",
    )
    assert leaking_value not in caplog.text
    assert any(
        getattr(record, "library_item_id", None) == 7
        and getattr(record, "library_item_fields", None) == error.value.field_names
        for record in caplog.records
    )


async def test_collection_index_endpoint_is_cursor_bounded_and_serialises_safe_tiles(
    monkeypatch: MonkeyPatch,
) -> None:
    async def page(
        _self: object, *, cursor: str | None, search: str | None
    ) -> tuple[tuple[CollectionTileView, ...], str | None]:
        assert cursor == "after-first"
        assert search == "Stargate"
        return (
            (
                CollectionTileView(
                    id=1,
                    name="Stargate",
                    itemCount=3,
                    watchOrderCount=1,
                    revision=2,
                    mosaicUrls=("/kanvas/artwork/7/8",),
                ),
            ),
            None,
        )

    monkeypatch.setattr("kasana.kanvas.dashboard.KanvasKatalogService.collection_page", page)
    request = Request(
        {
            "type": "http",
            "query_string": b"cursor=after-first&search=Stargate",
            "headers": [],
        }
    )

    response = await collections_data(request)

    assert response.status_code == 200
    payload = json.loads(bytes(response.body))
    assert payload["nextCursor"] is None
    assert payload["items"] == [
        {
            "id": 1,
            "name": "Stargate",
            "itemCount": 3,
            "watchOrderCount": 1,
            "revision": 2,
            "artworkUrl": None,
            "mosaicUrls": ["/kanvas/artwork/7/8"],
        }
    ]


async def test_collection_member_conflict_preserves_browser_intent_for_reapply(
    monkeypatch: MonkeyPatch,
) -> None:
    class ConflictCatalogue:
        def __init__(self, _settings: Kanvas_Settings, _user_id: int | None = None) -> None:
            pass

        async def add_collection_member(self, _collection_id: int, **_arguments: object) -> int:
            raise KatalogClientError(KatalogClientErrorKind.CONFLICT, "revision changed")

        async def collection_detail(self, _collection_id: int) -> SimpleNamespace:
            return SimpleNamespace(revision=8)

    class JsonRequest:
        async def json(self) -> object:
            return {"operation": "add", "revision": 7, "itemId": 12}

    monkeypatch.setattr("kasana.kanvas.dashboard.KanvasKatalogService", ConflictCatalogue)

    response = await collection_member_action(4, cast(Request, JsonRequest()))

    assert response.status_code == 409
    payload = json.loads(bytes(response.body))
    assert payload["intent"] == {"operation": "add", "revision": 7, "itemId": 12}
    assert payload["currentRevision"] == 8
    assert payload["reloadUrl"] == "/collections/4/edit"


async def test_collection_and_watch_order_action_routes_use_explicit_public_mutations(
    monkeypatch: MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []

    class FakeCatalogue:
        def __init__(self, _settings: Kanvas_Settings, _user_id: int | None = None) -> None:
            pass

        async def create_collection(self, *, name: str, overview: str | None) -> int:
            calls.append(("create-collection", (name, overview)))
            return 4

        async def update_collection(self, collection_id: int, **arguments: object) -> int:
            calls.append(("update-collection", (collection_id, arguments)))
            return 3

        async def delete_collection(self, collection_id: int, *, revision: int) -> None:
            calls.append(("delete-collection", (collection_id, revision)))

        async def update_collection_member(self, collection_id: int, **arguments: object) -> int:
            calls.append(("update-member", (collection_id, arguments)))
            return 4

        async def remove_collection_member(
            self, collection_id: int, **arguments: object
        ) -> tuple[int, tuple[str, ...]]:
            calls.append(("remove-member", (collection_id, arguments)))
            return 5, ()

        async def create_watch_order(self, collection_id: int, **arguments: object) -> int:
            calls.append(("create-order", (collection_id, arguments)))
            return 9

        async def update_watch_order(self, watch_order_id: int, **arguments: object) -> int:
            calls.append(("update-order", (watch_order_id, arguments)))
            return 6

        async def delete_watch_order(self, watch_order_id: int, *, revision: int) -> int:
            calls.append(("delete-order", (watch_order_id, revision)))
            return 3

        async def apply_generation(self, watch_order_id: int, **arguments: object) -> int:
            calls.append(("apply-generation", (watch_order_id, arguments)))
            return 7

    class FormRequest:
        def __init__(self, **values: str) -> None:
            self._form = FormData(values)

        async def form(self) -> FormData:
            return self._form

    monkeypatch.setattr("kasana.kanvas.dashboard.KanvasKatalogService", FakeCatalogue)

    created = await create_collection_action(
        cast(Request, FormRequest(name="Stargate", overview="Gate travel"))
    )
    updated = await update_collection_action(
        4, cast(Request, FormRequest(revision="2", name="Stargate SG-1", overview=""))
    )
    member = await update_collection_member_action(
        4,
        7,
        cast(Request, FormRequest(revision="3", relationship="spinoff")),
    )
    removed = await remove_collection_member_action(4, 7, cast(Request, FormRequest(revision="4")))
    collection_deleted = await delete_collection_action(
        4, cast(Request, FormRequest(revision="5", confirm="DELETE"))
    )
    order_created = await create_watch_order_action(
        4,
        cast(
            Request,
            FormRequest(collection_revision="6", name="Release", kind="custom"),
        ),
    )
    order_updated = await update_watch_order_action(
        9, cast(Request, FormRequest(revision="1", name="Air", kind="air"))
    )
    order_deleted = await delete_watch_order_action(
        9, cast(Request, FormRequest(revision="2", confirm="delete"))
    )
    generated = await apply_watch_order_generation_action(
        9,
        cast(Request, FormRequest(revision="3", mode="air", apply_mode="replace")),
    )

    assert created.headers["location"] == "/collections/4"
    assert updated.headers["location"] == "/collections/4"
    assert member.headers["location"] == "/collections/4/edit"
    assert removed.headers["location"] == "/collections/4/edit"
    assert collection_deleted.headers["location"] == "/collections"
    assert order_created.headers["location"] == "/watch-orders/9/edit"
    assert order_updated.headers["location"] == "/watch-orders/9/edit"
    assert order_deleted.headers["location"] == "/collections"
    assert generated.headers["location"] == "/watch-orders/9/edit"
    assert [name for name, _ in calls] == [
        "create-collection",
        "update-collection",
        "update-member",
        "remove-member",
        "delete-collection",
        "create-order",
        "update-order",
        "delete-order",
        "apply-generation",
    ]


async def test_browser_data_and_entry_actions_are_bounded_and_revision_guarded(
    monkeypatch: MonkeyPatch,
) -> None:
    calls: list[str] = []

    class FakeCatalogue:
        def __init__(self, _settings: Kanvas_Settings, _user_id: int | None = None) -> None:
            pass

        async def item_picker_page(
            self, collection_id: int, **arguments: object
        ) -> tuple[tuple[ItemPickerView, ...], str | None]:
            assert collection_id == 4
            assert arguments["playable_only"] is True
            calls.append("picker")
            return (
                (
                    ItemPickerView(
                        id=7,
                        title="Pilot",
                        kind="episode",
                        year=1997,
                        available=True,
                        alreadyMember=False,
                    ),
                ),
                "next",
            )

        async def watch_order_page(
            self, watch_order_id: int, **arguments: object
        ) -> tuple[tuple[WatchOrderRowView, ...], str | None, int]:
            assert watch_order_id == 9
            assert arguments["cursor"] == "later"
            calls.append("rows")
            return (
                (
                    WatchOrderRowView(
                        id=3,
                        position=0,
                        itemId=7,
                        title="Pilot",
                        kind="episode",
                        available=True,
                    ),
                ),
                None,
                6,
            )

        async def add_watch_order_entry(self, _watch_order_id: int, **_arguments: object) -> int:
            calls.append("add")
            return 7

        async def move_watch_order_entry(self, _watch_order_id: int, **_arguments: object) -> int:
            calls.append("move")
            return 8

        async def move_watch_order_entry_to_boundary(
            self, _watch_order_id: int, **_arguments: object
        ) -> int:
            calls.append("boundary")
            return 9

        async def remove_watch_order_entry(self, _watch_order_id: int, **_arguments: object) -> int:
            calls.append("remove")
            return 10

    class JsonRequest:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        async def json(self) -> object:
            return self._payload

    class FakePlayback:
        def __init__(self, _settings: Kanvas_Settings, _user_id: int | None = None) -> None:
            pass

        async def create_watch_order_launch_uri(
            self, watch_order_id: int, *, start_item_id: int | None
        ) -> str:
            assert (watch_order_id, start_item_id) == (9, 7)
            return "kasana://play/" + "A" * 32

    monkeypatch.setattr("kasana.kanvas.dashboard.KanvasKatalogService", FakeCatalogue)
    monkeypatch.setattr("kasana.kanvas.dashboard.KanvasPlaybackService", FakePlayback)

    picker_response = await collection_picker_data(
        4,
        Request({"type": "http", "query_string": b"search=pilot&playable=true", "headers": []}),
    )
    rows_response = await watch_order_data(
        9, Request({"type": "http", "query_string": b"cursor=later", "headers": []})
    )
    add = await watch_order_entry_action(
        9, cast(Request, JsonRequest({"operation": "add", "revision": 6, "itemId": 7}))
    )
    move = await watch_order_entry_action(
        9,
        cast(
            Request,
            JsonRequest(
                {
                    "operation": "move",
                    "revision": 7,
                    "entryId": 3,
                    "beforeEntryId": None,
                    "afterEntryId": None,
                }
            ),
        ),
    )
    boundary = await watch_order_entry_action(
        9,
        cast(
            Request,
            JsonRequest({"operation": "move", "revision": 8, "entryId": 3, "boundary": "end"}),
        ),
    )
    removed = await watch_order_entry_action(
        9, cast(Request, JsonRequest({"operation": "remove", "revision": 9, "entryId": 3}))
    )
    launched = await watch_order_launch_action(9, cast(Request, JsonRequest({"itemId": 7})))

    assert json.loads(bytes(picker_response.body))["nextCursor"] == "next"
    assert json.loads(bytes(rows_response.body))["revision"] == 6
    assert json.loads(bytes(launched.body))["launchUri"] == "kasana://play/" + "A" * 32
    assert [
        json.loads(bytes(response.body))["revision"] for response in (add, move, boundary, removed)
    ] == [
        7,
        8,
        9,
        10,
    ]
    assert calls == ["picker", "rows", "add", "move", "boundary", "remove"]


async def test_artwork_proxy_and_invalid_browser_actions_have_local_failure_states(
    monkeypatch: MonkeyPatch,
) -> None:
    class ArtworkCatalogue:
        def __init__(self, _settings: Kanvas_Settings, _user_id: int | None = None) -> None:
            pass

        async def artwork_content(
            self, item_id: int, _artwork_id: int
        ) -> tuple[bytes, str, str | None]:
            if item_id == 8:
                raise KatalogClientError(KatalogClientErrorKind.NOT_FOUND, "gone")
            if item_id == 9:
                raise KatalogClientError(KatalogClientErrorKind.UNAVAILABLE, "offline")
            return b"art", "image/jpeg", "etag-value"

    class JsonRequest:
        async def json(self) -> object:
            return {"operation": "unknown", "revision": 1}

    monkeypatch.setattr("kasana.kanvas.dashboard.KanvasKatalogService", ArtworkCatalogue)

    response = await artwork(7, 8)
    invalid = await collection_member_action(4, cast(Request, JsonRequest()))

    assert response.body == b"art"
    assert response.headers["etag"] == "etag-value"
    assert response.headers["cache-control"] == "private, max-age=3600"
    assert invalid.status_code == 422
    with pytest.raises(HTTPException, match="Artwork was not found"):
        await artwork(8, 8)
    with pytest.raises(HTTPException, match="Artwork is unavailable"):
        await artwork(9, 8)


async def test_browser_data_endpoints_return_typed_katalog_failure_states(
    monkeypatch: MonkeyPatch,
) -> None:
    class FailingCatalogue:
        def __init__(self, _settings: Kanvas_Settings, _user_id: int | None = None) -> None:
            pass

        async def collection_page(self, **_arguments: object) -> object:
            raise KatalogClientError(KatalogClientErrorKind.TRANSPORT, "offline")

        async def item_picker_page(self, _collection_id: int, **_arguments: object) -> object:
            raise KatalogClientError(KatalogClientErrorKind.UNAVAILABLE, "offline")

        async def watch_order_page(self, _watch_order_id: int, **_arguments: object) -> object:
            raise KatalogClientError(KatalogClientErrorKind.TRANSPORT, "offline")

    monkeypatch.setattr("kasana.kanvas.dashboard.KanvasKatalogService", FailingCatalogue)

    collections = await collections_data(
        Request({"type": "http", "query_string": b"", "headers": []})
    )
    picker = await collection_picker_data(
        4, Request({"type": "http", "query_string": b"", "headers": []})
    )
    entries = await watch_order_data(
        9, Request({"type": "http", "query_string": b"", "headers": []})
    )
    invalid_library = await library_data(
        Request({"type": "http", "query_string": b"year=invalid", "headers": []})
    )

    assert [
        response.status_code for response in (collections, picker, entries, invalid_library)
    ] == [
        503,
        503,
        503,
        422,
    ]


async def test_administration_data_and_mutation_endpoints_stay_within_katalog_boundary(
    monkeypatch: MonkeyPatch,
) -> None:
    calls: list[str] = []
    overview = AdministrationOverviewView(
        connected=True,
        databaseRevision="revision",
        databaseHealthy=True,
        enabledRootCount=1,
        unavailableRootCount=0,
        unresolvedMetadataCount=1,
        activeJobCount=1,
        failedJobCount=0,
        interruptedJobCount=0,
        artworkCacheSizeBytes=8,
        artworkCacheFileCount=1,
    )
    root = LibraryRootView(
        id=1,
        displayName="Films",
        path="/media",
        kind="movie",
        enabled=True,
        available=True,
        itemCount=2,
        mediaFileCount=3,
    )
    review = MetadataReviewItemView(
        itemId=7,
        title="Local title",
        kind="movie",
        candidates=(
            MetadataCandidateView(
                id=9,
                provider="tmdb",
                providerId="42",
                title="Candidate",
                kind="movie",
                confidence=0.9,
                status="suggested",
            ),
        ),
    )

    class AdminCatalogue:
        def __init__(self, _settings: Kanvas_Settings, _user_id: int | None = None) -> None:
            pass

        async def administration_overview(self) -> AdministrationOverviewView:
            return overview

        async def administration_jobs(
            self, *, cursor: str | None
        ) -> tuple[tuple[JobView, ...], str | None]:
            assert cursor is None
            return (_admin_job(),), "next"

        async def administration_roots(self) -> tuple[LibraryRootView, ...]:
            return (root,)

        async def administration_directories(self, path: str | None) -> DirectoryListing:
            assert path == "/media"
            return DirectoryListing(
                path="/media",
                parent_path="/",
                entries=(DirectoryEntry(name="Movies", path="/media/Movies"),),
            )

        async def metadata_review_items(
            self, *, cursor: str | None
        ) -> tuple[tuple[MetadataReviewItemView, ...], str | None]:
            assert cursor is None
            return (review,), None

        async def submit_scan(self, _request: object) -> JobView:
            calls.append("scan")
            return _admin_job()

        async def submit_library_consistency(
            self, request: LibraryConsistencyRequest
        ) -> JobView:
            calls.append("consistency")
            assert request.library_root_id == 1
            assert request.include_unavailable is True
            assert request.dry_run is False
            return _admin_job()

        async def submit_artwork_fetch(self, _request: object) -> JobView:
            calls.append("artwork")
            return _admin_job()

        async def cancel_job(self, _job_id: str) -> JobView:
            calls.append("cancel")
            return _admin_job()

        async def match_metadata_candidate(self, _item_id: int, **_kwargs: str) -> None:
            calls.append("match")

        async def reject_metadata_candidate(self, _item_id: int, **_kwargs: str) -> None:
            calls.append("reject")

        async def ignore_metadata_item(self, _item_id: int) -> None:
            calls.append("ignore")

        async def refresh_metadata_item(self, _item_id: int) -> None:
            calls.append("refresh")

        async def create_library_root(self, request: LibraryRootCreate) -> LibraryRootSummary:
            calls.append("create-root")
            assert request.expected_kind is LibraryRootKind.MOVIE
            return LibraryRootSummary(
                id=1,
                path="media",
                expected_kind=LibraryRootKind.MOVIE,
                enabled=True,
                available=True,
                item_count=0,
                media_file_count=0,
            )

        async def update_library_root(
            self, _root_id: int, request: LibraryRootUpdate
        ) -> LibraryRootSummary:
            calls.append("update-root")
            assert request.enabled is False
            return await self.create_library_root(
                LibraryRootCreate(path="media", expected_kind=LibraryRootKind.MOVIE)
            )

        async def delete_library_root(self, _root_id: int, *, confirm: bool) -> None:
            assert confirm is True
            calls.append("delete-root")

    class JsonRequest:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        async def json(self) -> object:
            return self._payload

    monkeypatch.setattr("kasana.kanvas.dashboard.KanvasKatalogService", AdminCatalogue)
    request = Request({"type": "http", "query_string": b"", "headers": []})
    overview_response = await administration_overview_data(request)
    jobs_response = await administration_jobs_data(
        Request({"type": "http", "query_string": b"", "headers": []})
    )
    roots_response = await administration_roots_data(request)
    directories_response = await administration_directories_data(
        Request({"type": "http", "query_string": b"path=/media", "headers": []})
    )
    metadata_response = await administration_metadata_data(
        Request({"type": "http", "query_string": b"", "headers": []})
    )
    payloads: tuple[dict[str, object], ...] = (
        {"operation": "scan", "rootId": 1, "dryRun": True},
        {"operation": "library-consistency", "rootId": 1, "includeUnavailable": True},
        {"operation": "artwork-fetch"},
        {"operation": "cancel-job", "jobId": "job-1"},
        {"operation": "match", "itemId": 7, "provider": "tmdb", "providerId": "42"},
        {"operation": "reject", "itemId": 7, "provider": "tmdb", "providerId": "42"},
        {"operation": "ignore", "itemId": 7},
        {"operation": "refresh", "itemId": 7},
        {"operation": "root-create", "path": "media", "kind": "movie", "tags": ["films"]},
        {"operation": "root-update", "rootId": 1, "enabled": False, "tags": list[str]()},
        {"operation": "root-delete", "rootId": 1, "confirm": True},
    )
    actions = [
        await administration_action(cast(Request, JsonRequest(payload))) for payload in payloads
    ]

    assert json.loads(bytes(overview_response.body))["connected"] is True
    assert json.loads(bytes(jobs_response.body))["nextCursor"] == "next"
    assert json.loads(bytes(roots_response.body))["items"][0]["displayName"] == "Films"
    assert json.loads(bytes(roots_response.body))["items"][0]["path"] == "/media"
    assert json.loads(bytes(directories_response.body))["entries"][0]["path"] == "/media/Movies"
    assert json.loads(bytes(metadata_response.body))["items"][0]["itemId"] == 7
    assert all(response.status_code == 200 for response in actions)
    assert calls == [
        "scan",
        "consistency",
        "artwork",
        "cancel",
        "match",
        "reject",
        "ignore",
        "refresh",
        "create-root",
        "update-root",
        "create-root",
        "delete-root",
    ]


async def test_katalog_administration_service_transforms_only_public_contracts(
    monkeypatch: MonkeyPatch,
) -> None:
    calls: list[str] = []
    root = LibraryRootSummary(
        id=1,
        path="media",
        display_name="Films",
        expected_kind=LibraryRootKind.MOVIE,
        enabled=True,
        available=True,
        item_count=3,
        media_file_count=4,
    )
    candidate = MetadataReviewCandidate(
        item_id=7,
        candidate_id=9,
        provider="tmdb",
        provider_id="42",
        title="Candidate",
        kind=LibraryItemKind.MOVIE,
        confidence=0.9,
        status="suggested",
    )
    job = BackgroundJob(
        id="job-1",
        kind="scan",
        status=JobStatus.RUNNING,
        submitted_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
        completed_at=None,
        progress=JobProgress(phase="scan", current=1, total=2, unit="files"),
    )
    local = cast(
        LibraryItemDetail,
        SimpleNamespace(
            id=7,
            title="Local title",
            year=2004,
            kind=LibraryItemKind.MOVIE,
            artwork=(),
        ),
    )

    class FakeClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def status(self) -> StatusResponse:
            return StatusResponse(
                database_revision="revision",
                item_count=3,
                media_file_count=4,
                available_file_count=4,
                unresolved_audit_issue_count=0,
                active_job_count=1,
                failed_job_count=0,
            )

        async def list_library_roots(self) -> tuple[LibraryRootSummary, ...]:
            return (root,)

        async def browse_library_directories(self, path: str | None) -> DirectoryListing:
            assert path == "/media"
            return DirectoryListing(path="/media", entries=())

        async def metadata_review(
            self, *, cursor: str | None = None, limit: int = 50
        ) -> PaginatedResponse[MetadataReviewCandidate]:
            assert limit in {50, 100}
            return PaginatedResponse(items=(candidate,), next_cursor=cursor, limit=limit)

        async def list_jobs(
            self, *, cursor: str | None, limit: int
        ) -> PaginatedResponse[BackgroundJob]:
            assert limit == 50
            return PaginatedResponse(items=(job,), next_cursor=cursor, limit=limit)

        async def get_library_item(self, item_id: int) -> object:
            assert item_id == 7
            return SimpleNamespace(item=local)

        async def match_metadata(self, *_args: object) -> None:
            calls.append("match")

        async def reject_metadata(self, *_args: object) -> None:
            calls.append("reject")

        async def ignore_metadata(self, *_args: object) -> None:
            calls.append("ignore")

        async def refresh_metadata(self, *_args: object) -> None:
            calls.append("refresh")

        async def submit_scan(self, *_args: object) -> SimpleNamespace:
            calls.append("scan")
            return SimpleNamespace(job=job)

        async def submit_library_consistency(self, *_args: object) -> SimpleNamespace:
            calls.append("consistency")
            return SimpleNamespace(job=job)

        async def submit_artwork_fetch(self, *_args: object) -> SimpleNamespace:
            calls.append("artwork")
            return SimpleNamespace(job=job)

        async def cancel_job(self, *_args: object) -> BackgroundJob:
            calls.append("cancel")
            return job

        async def create_library_root(self, request: LibraryRootCreate) -> LibraryRootSummary:
            calls.append("create-root")
            assert request.path == "media"
            return root

        async def update_library_root(
            self, _root_id: int, request: LibraryRootUpdate
        ) -> LibraryRootSummary:
            calls.append("update-root")
            assert request.enabled is False
            return root

        async def delete_library_root(self, _root_id: int, *, confirm: bool) -> None:
            assert confirm is True
            calls.append("delete-root")

    monkeypatch.setattr("kasana.kanvas.services.katalog.KatalogClient", FakeClient)
    service = KanvasKatalogService(Kanvas_Settings())
    overview = await service.administration_overview()
    jobs, next_jobs = await service.administration_jobs(cursor="jobs")
    roots = await service.administration_roots()
    directories = await service.administration_directories("/media")
    review, next_review = await service.metadata_review_items(cursor="review")
    await service.match_metadata_candidate(7, provider="tmdb", provider_id="42")
    await service.reject_metadata_candidate(7, provider="tmdb", provider_id="42")
    await service.ignore_metadata_item(7)
    await service.refresh_metadata_item(7)
    await service.submit_scan(ScanRequest(library_root_id=1))
    await service.submit_library_consistency(LibraryConsistencyRequest(library_root_id=1))
    await service.submit_artwork_fetch(ArtworkFetchRequest(library_root_id=1))
    await service.cancel_job("job-1")
    await service.create_library_root(
        LibraryRootCreate(path="media", expected_kind=LibraryRootKind.MOVIE)
    )
    await service.update_library_root(1, LibraryRootUpdate(enabled=False))
    await service.delete_library_root(1, confirm=True)

    assert overview.unresolved_metadata_count == 1
    assert (jobs[0].phase, next_jobs) == ("scan", "jobs")
    assert roots[0].display_name == "Films"
    assert roots[0].path == "media"
    assert directories.path == "/media"
    assert (review[0].title, review[0].candidates[0].provider_id, next_review) == (
        "Local title",
        "42",
        "review",
    )
    assert calls == [
        "match",
        "reject",
        "ignore",
        "refresh",
        "scan",
        "consistency",
        "artwork",
        "cancel",
        "create-root",
        "update-root",
        "delete-root",
    ]


async def test_katalog_service_item_edit_contracts(
    monkeypatch: MonkeyPatch,
) -> None:
    item = _editable_item()
    audit = LibraryItemEditAudit(
        id=3,
        actor="tester",
        changed_fields=("title", "selected_artwork"),
        occurred_at=datetime.now(UTC),
    )
    mutation = LibraryItemMutationResult(item=item, audit=audit)
    calls: list[str] = []

    class FakeClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get_library_item(self, item_id: int) -> SimpleNamespace:
            assert item_id == 7
            calls.append("detail")
            return SimpleNamespace(item=item)

        async def update_library_item(
            self, item_id: int, request: LibraryItemUpdate
        ) -> LibraryItemMutationResult:
            assert item_id == 7
            assert request.title == "Updated"
            assert request.selected_artwork == (
                SelectedArtwork(kind=ArtworkKind.POSTER, artwork_id=8),
            )
            calls.append("update")
            return mutation

        async def list_library_item_edit_audit(
            self, item_id: int
        ) -> tuple[LibraryItemEditAudit, ...]:
            assert item_id == 7
            calls.append("audit")
            return (audit,)

    monkeypatch.setattr("kasana.kanvas.services.katalog.KatalogClient", FakeClient)
    service = KanvasKatalogService(Kanvas_Settings(), user_id=1)

    detail = await service.item_edit_detail(7)
    result = await service.update_item(
        7,
        LibraryItemUpdate(
            actor="tester",
            title="Updated",
            selected_artwork=(SelectedArtwork(kind=ArtworkKind.POSTER, artwork_id=8),),
        ),
    )
    audits = await service.item_edit_audit(7)

    assert detail.title == "A title"
    assert result.audit.changed_fields == ("title", "selected_artwork")
    assert audits == (audit,)
    assert calls == ["detail", "update", "audit"]


async def test_item_edit_endpoints_report_data_and_validation(
    monkeypatch: MonkeyPatch,
) -> None:
    item = _editable_item(title="Updated")
    audit = LibraryItemEditAudit(
        id=3,
        actor="tester",
        changed_fields=("title", "tags"),
        occurred_at=datetime.now(UTC),
    )

    class EditingCatalogue:
        def __init__(self, _settings: Kanvas_Settings, _user_id: int | None = None) -> None:
            assert _user_id == 1

        async def item_edit_detail(self, item_id: int) -> LibraryItemDetail:
            assert item_id == 7
            return item

        async def item_edit_audit(self, item_id: int) -> tuple[LibraryItemEditAudit, ...]:
            assert item_id == 7
            return (audit,)

        async def update_item(
            self, item_id: int, request: LibraryItemUpdate
        ) -> LibraryItemMutationResult:
            assert item_id == 7
            assert request.actor == "tester"
            assert request.title == "Updated"
            assert request.sort_title == "Updated"
            assert request.release_year == 2004
            assert request.tags == ("anime", "favourite")
            assert request.locked_metadata_fields == ("title",)
            assert request.selected_artwork == (
                SelectedArtwork(kind=ArtworkKind.POSTER, artwork_id=8),
            )
            return LibraryItemMutationResult(item=item, audit=audit)

    class JsonRequest:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        async def json(self) -> object:
            return self._payload

    monkeypatch.setattr("kasana.kanvas.dashboard.KanvasKatalogService", EditingCatalogue)
    detail_response = await item_edit_data(
        7, Request({"type": "http", "query_string": b"", "headers": []})
    )
    action_response = await item_edit_action(
        7,
        cast(
            Request,
            JsonRequest(
                {
                    "title": "Updated",
                    "sortTitle": "Updated",
                    "overview": "An overview",
                    "releaseDate": "2004-02-03",
                    "releaseYear": 2004,
                    "tags": ["anime", "favourite"],
                    "seasonNumber": None,
                    "episodeNumber": None,
                    "lockedMetadataFields": ["title"],
                    "selectedArtwork": [{"kind": "poster", "artworkId": 8}],
                    "kind": "movie",
                    "parentId": None,
                }
            ),
        ),
    )
    invalid_action_response = await item_edit_action(
        7, cast(Request, JsonRequest({"tags": "anime"}))
    )

    assert json.loads(bytes(detail_response.body))["item"]["selected_artwork"] == [
        {"kind": "poster", "artwork_id": 8}
    ]
    assert json.loads(bytes(detail_response.body))["audit"][0]["actor"] == "tester"
    assert json.loads(bytes(action_response.body))["audit"]["changed_fields"] == ["title", "tags"]
    assert invalid_action_response.status_code == 422


async def test_administration_error_states_and_local_section_routes(
    monkeypatch: MonkeyPatch,
) -> None:
    class FailingAdminCatalogue:
        def __init__(self, _settings: Kanvas_Settings, _user_id: int | None = None) -> None:
            pass

        async def administration_overview(self) -> object:
            raise KatalogClientError(KatalogClientErrorKind.UNAVAILABLE, "offline")

        async def administration_jobs(self, **_arguments: object) -> object:
            raise KatalogClientError(KatalogClientErrorKind.TRANSPORT, "offline")

        async def administration_roots(self) -> object:
            raise KatalogClientError(KatalogClientErrorKind.TRANSPORT, "offline")

        async def metadata_review_items(self, **_arguments: object) -> object:
            raise KatalogClientError(KatalogClientErrorKind.UNAVAILABLE, "offline")

    class JsonRequest:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        async def json(self) -> object:
            return self._payload

    monkeypatch.setattr("kasana.kanvas.dashboard.KanvasKatalogService", FailingAdminCatalogue)
    responses = (
        await administration_overview_data(
            Request({"type": "http", "query_string": b"", "headers": []})
        ),
        await administration_jobs_data(
            Request({"type": "http", "query_string": b"", "headers": []})
        ),
        await administration_roots_data(
            Request({"type": "http", "query_string": b"", "headers": []})
        ),
        await administration_metadata_data(
            Request({"type": "http", "query_string": b"", "headers": []})
        ),
        await administration_action(cast(Request, JsonRequest({"operation": "unknown"}))),
        await administration_action(
            cast(Request, JsonRequest({"operation": "root-delete", "rootId": 1}))
        ),
        await administration_action(cast(Request, JsonRequest({"operation": "root-create"}))),
    )

    assert [response.status_code for response in responses] == [503, 503, 503, 503, 422, 422, 422]
    with Client(page("")):
        request = Request({"type": "http", "query_string": b"", "headers": []})
        await administration_page(request)
        await administration_metadata_page(request)
        await administration_libraries_page(request)
        await administration_jobs_page(request)
        await administration_artwork_page(request)


def test_native_component_builders_cover_poster_rail_feedback_and_shell() -> None:
    settings = Kanvas_Settings()
    poster = PosterView(id=7, title="Poster", href="/item/7", available=True)

    with Client(page("")):
        with page_shell(settings, "/library", "Library"):
            primary_navigation("/library")
            poster_card(poster)
            progress_indicator(None)
            progress_indicator(50)
            media_rail(MediaRailView(title="Empty", posters=()))
            media_rail(MediaRailView(title="Items", posters=(poster,)))
            feedback_state("Empty", "No entries")
            feedback_state("Retry", "Try again", retry=lambda: None)
        skeleton_posters(2)


def test_shell_does_not_mount_search_overlay() -> None:
    with Client(page("")) as client:
        with page_shell(Kanvas_Settings(), "/library", "Library", _selected_profile()):
            pass
        search_actions = [
            element
            for element in client.elements.values()
            if _element_props(element).get("data-kanvas-global-search") == "true"
        ]
        overlays = [
            element for element in client.elements.values() if element.tag == "kanvas-global-search"
        ]

    assert search_actions == []
    assert overlays == []


def test_administration_sections_mount_distinct_browser_states_and_active_tabs() -> None:
    sections = ("overview", "metadata", "libraries", "jobs", "artwork", "hierarchy")

    for section in sections:
        with Client(page("")) as client:
            render_administration(Kanvas_Settings(), _selected_profile(), section)
            administration = next(
                element
                for element in client.elements.values()
                if element.tag == BrowserComponent.ADMINISTRATION
            )
            active_tabs = [
                element
                for element in client.elements.values()
                if "k-admin-nav__link--active" in _element_classes(element)
            ]

        assert _element_props(administration)["data-section"] == section
        assert "section" not in _element_props(administration)
        assert len(active_tabs) == 1


def test_browser_component_mounting_uses_native_elements_and_validates_attributes() -> None:
    with Client(page("")) as client:
        mount_browser_component(
            BrowserComponent.ITEM_PICKER,
            {
                "label": 'Add "quoted" item',
                "revision": 3,
                "playable-only": True,
            },
        )
        element = next(
            element
            for element in client.elements.values()
            if element.tag == BrowserComponent.ITEM_PICKER
        )

    props = _element_props(element)
    assert props["label"] == 'Add "quoted" item'
    assert props["revision"] == "3"
    assert props["playable-only"] == "true"

    with pytest.raises(ValueError, match="kebab-case"):
        mount_browser_component(BrowserComponent.POSTER_GRID, {"bad_attribute": "value"})


def test_collection_components_cover_empty_states_and_shared_textarea() -> None:
    empty_preview = GenerationPreviewView(
        watchOrderId=1,
        revision=1,
        mode="air",
        applyMode="replace",
        entries=(),
    )

    with Client(page("")) as client:
        render_collection_artwork("/kanvas/artwork/1/1", (), "Artwork")
        render_collection_artwork(None, (), "Fallback")
        textarea_input(
            name="notes",
            aria_label="Notes",
            value='A "quoted" note',
            placeholder="Optional notes",
        )
        generation_preview(empty_preview, apply_action="/kanvas/actions/watch-orders/1/apply")

        textarea = next(
            element for element in client.elements.values() if element.tag == "textarea"
        )
        fallback = next(
            element
            for element in client.elements.values()
            if "k-collection-art__fallback" in _element_classes(element)
        )

    assert _element_props(textarea)["value"] == 'A "quoted" note'
    assert _element_props(textarea)["placeholder"] == "Optional notes"
    assert fallback.tag == "div"


def test_native_icon_builder_rejects_unknown_icon() -> None:
    from kasana.kanvas.components.controls import icon_svg

    with pytest.raises(ValueError, match="Unknown Kanvas icon"):
        icon_svg("not-an-icon")


async def test_visual_routes_render_with_fake_katalog_data(monkeypatch: MonkeyPatch) -> None:
    poster = PosterView(id=7, title="Poster", href="/item/7", available=True)

    class HomeCatalogue:
        def __init__(self, _settings: Kanvas_Settings, _user_id: int | None = None) -> None:
            pass

        async def home_rails(self) -> tuple[MediaRailView, ...]:
            return (MediaRailView(title="Continue", posters=(poster,)),)

    class ItemCatalogue:
        def __init__(self, _settings: Kanvas_Settings, _user_id: int | None = None) -> None:
            pass

        async def item_detail(self, item_id: int) -> ItemDetailView:
            assert item_id == 7
            return ItemDetailView(
                id=7,
                title="Poster",
                kind="movie",
                posterUrl="/kanvas/artwork/7/8",
                runtimeLabel="90m",
                progressPercent=30,
                available=True,
                children=(poster,),
            )

        async def mark_watched(self, _item_id: int) -> None:
            pass

        async def clear_watched(self, _item_id: int) -> None:
            pass

        async def library_tags(self) -> tuple[str, ...]:
            return ("anime",)

    with Client(page("")) as client:
        monkeypatch.setattr(home_route, "KanvasKatalogService", HomeCatalogue)
        await home_route.render_home(Kanvas_Settings(), _selected_profile())
        monkeypatch.setattr(item_route, "KanvasKatalogService", ItemCatalogue)
        await item_route.render_item(Kanvas_Settings(), _selected_profile(), 7)
        monkeypatch.setattr(library_route, "KanvasKatalogService", ItemCatalogue)
        await render_library(
            Kanvas_Settings(), _selected_profile(), LibraryFilters(search="poster")
        )
        await collections_page(Request({"type": "http", "query_string": b"", "headers": []}))
        request = Request({"type": "http", "query_string": b"", "headers": []})
        await administration_page(request)
        await design_page()

        browser_components = [
            element for element in client.elements.values() if element.tag in set(BrowserComponent)
        ]
        assert browser_components
        assert all(element.tag != "nicegui-html" for element in browser_components)


async def test_collection_and_watch_order_routes_render_the_editor_states(
    monkeypatch: MonkeyPatch,
) -> None:
    poster = PosterView(
        id=7,
        title="Pilot",
        href="/item/7",
        posterUrl="/kanvas/artwork/7/8",
        available=True,
    )
    member = CollectionMemberView(poster=poster, kind="movie", relationship="primary")
    collection = CollectionDetailView(
        id=4,
        name="Stargate",
        overview="Gate travel",
        itemCount=1,
        watchOrderCount=1,
        revision=3,
        mosaicUrls=("/kanvas/artwork/7/8",),
        movies=(member,),
        memberNextCursor="next-members",
        watchOrders=(
            WatchOrderCardView(
                id=9,
                collectionId=4,
                name="Release",
                kind="custom",
                entryCount=1,
                revision=2,
                progressPercent=25,
                nextItemTitle="Pilot",
            ),
        ),
    )
    editor = WatchOrderEditorView(
        id=9,
        collectionId=4,
        collectionName="Stargate",
        name="Release",
        kind="custom",
        entryCount=1,
        revision=2,
    )
    preview = GenerationPreviewView(
        watchOrderId=9,
        revision=2,
        mode="air",
        applyMode="replace",
        entries=(
            WatchOrderRowView(
                id=1,
                position=0,
                itemId=7,
                title="Pilot",
                kind="episode",
                available=True,
            ),
        ),
        undatedTitles=("Special",),
        unavailableTitles=("Missing",),
        duplicateTitles=("Pilot",),
        nonPlayableTitles=("Series",),
        removedEntryTitles=("Old entry",),
    )

    class RouteCatalogue:
        def __init__(self, _settings: Kanvas_Settings, _user_id: int | None = None) -> None:
            pass

        async def collection_detail(self, collection_id: int) -> CollectionDetailView:
            assert collection_id == 4
            return collection

        async def watch_order_editor(self, watch_order_id: int) -> WatchOrderEditorView:
            assert watch_order_id == 9
            return editor

        async def generation_preview(
            self, watch_order_id: int, **arguments: object
        ) -> GenerationPreviewView:
            assert watch_order_id == 9
            assert arguments["revision"] == 2
            return preview

    monkeypatch.setattr(collections_route, "KanvasKatalogService", RouteCatalogue)

    with Client(page("")) as client:
        await collections_route.render_collection_new(Kanvas_Settings(), _selected_profile())
        await collections_route.render_collection_detail(Kanvas_Settings(), _selected_profile(), 4)
        await collections_route.render_collection_edit(Kanvas_Settings(), _selected_profile(), 4)
        await collections_route.render_watch_order_new(Kanvas_Settings(), _selected_profile(), 4)
        await collections_route.render_watch_order(
            Kanvas_Settings(),
            _selected_profile(),
            9,
            editable=True,
            preview_mode="air",
            apply_mode="replace",
        )

        browser_components = [
            element for element in client.elements.values() if element.tag in set(BrowserComponent)
        ]
        textareas = [element for element in client.elements.values() if element.tag == "textarea"]
        hidden_fields = [
            element
            for element in client.elements.values()
            if element.tag == "input" and _element_props(element).get("type") == "hidden"
        ]
        generation_entries = next(
            element
            for element in client.elements.values()
            if "k-generation-preview__entries" in _element_classes(element)
        )

    assert generation_entries.tag == "ol"
    assert browser_components
    assert all(element.tag != "nicegui-html" for element in browser_components)
    assert any(_element_props(element).get("name") == "overview" for element in textareas)
    assert any(_element_props(element).get("value") == "Gate travel" for element in textareas)
    assert any(_element_props(element).get("name") == "revision" for element in hidden_fields)


async def test_collection_routes_share_one_unavailable_state(monkeypatch: MonkeyPatch) -> None:
    class UnavailableCatalogue:
        def __init__(self, _settings: Kanvas_Settings, _user_id: int | None = None) -> None:
            pass

        async def collection_detail(self, _collection_id: int) -> CollectionDetailView:
            raise KatalogClientError(KatalogClientErrorKind.UNAVAILABLE, "offline")

        async def watch_order_editor(self, _watch_order_id: int) -> WatchOrderEditorView:
            raise KatalogClientError(KatalogClientErrorKind.UNAVAILABLE, "offline")

    monkeypatch.setattr(collections_route, "KanvasKatalogService", UnavailableCatalogue)

    with Client(page("")) as client:
        await collections_route.render_collection_detail(Kanvas_Settings(), _selected_profile(), 4)
        await collections_route.render_collection_edit(Kanvas_Settings(), _selected_profile(), 4)
        await collections_route.render_watch_order_new(Kanvas_Settings(), _selected_profile(), 4)
        await collections_route.render_watch_order(
            Kanvas_Settings(), _selected_profile(), 9, editable=True
        )

        feedback_titles = [
            element
            for element in client.elements.values()
            if "k-feedback__title" in _element_classes(element)
        ]

    assert len(feedback_titles) == 4


async def test_native_forms_and_design_review_use_shared_ui_primitives(
    monkeypatch: MonkeyPatch,
) -> None:
    class TagCatalogue:
        def __init__(self, _settings: Kanvas_Settings, _user_id: int | None = None) -> None:
            pass

        async def library_tags(self) -> tuple[str, ...]:
            return ("anime", "favourite")

    monkeypatch.setattr(library_route, "KanvasKatalogService", TagCatalogue)
    with Client(page("")) as library_client:
        await render_library(
            Kanvas_Settings(),
            _selected_profile(),
            LibraryFilters(
                search="poster",
                kind=LibraryItemKind.MOVIE,
                tags=("anime",),
                watched=WatchedFilter.IN_PROGRESS,
                availability=Availability.AVAILABLE,
            ),
        )
        library_search = _input_named(library_client, "search")

        assert "k-input" in _element_classes(library_search)
        assert _element_props(library_search)["type"] == "search"
        assert _element_props(library_search)["value"] == "poster"
        assert _element_props(library_search)["autofocus"] is True
        assert "k-control-shell" in _element_classes(_parent_element(library_search))
        assert "k-input-shell" in _element_classes(_parent_element(library_search))
        library_year = _input_named(library_client, "year")
        assert "k-input" in _element_classes(library_year)
        assert "k-input--year" in _element_classes(library_year)
        assert "k-input-shell--year" in _element_classes(_parent_element(library_year))
        for name in ("kind", "watched", "availability"):
            select = _select_named(library_client, name)
            assert "k-select" in _element_classes(select)
            assert "k-control-shell" in _element_classes(_parent_element(select))
            assert "k-select-wrap" in _element_classes(_parent_element(select))
        tag_inputs = [
            element
            for element in library_client.elements.values()
            if element.tag == "input" and _element_props(element).get("name") == "tag"
        ]
        tag_menu = _parent_element(_parent_element(_parent_element(tag_inputs[0])))
        assert {str(_element_props(tag)["value"]) for tag in tag_inputs} == {
            "anime",
            "favourite",
        }
        assert any(_element_props(tag).get("checked") is True for tag in tag_inputs)
        assert "k-check-menu" in _element_classes(tag_menu)
        apply_button = next(
            element
            for element in library_client.elements.values()
            if element.tag == "button" and _element_props(element).get("aria-label") == "Apply"
        )
        poster_grid = next(
            element
            for element in library_client.elements.values()
            if element.tag == BrowserComponent.POSTER_GRID
        )
        assert _element_props(apply_button)["type"] == "submit"
        assert _element_props(poster_grid)["state-user"] == "1"
        assert _element_props(poster_grid)["development-mode"] == "false"
        assert "search=poster" in cast(str, _element_props(poster_grid)["source"])
        assert "kind=movie" in cast(str, _element_props(poster_grid)["source"])

    with Client(page("")) as collections_client:
        await collections_route.render_collections_index(
            Kanvas_Settings(), _selected_profile(), search=None
        )
        collections_search = _input_named(collections_client, "search")

        assert _element_props(collections_search)["autofocus"] is True

    with Client(page("")) as design_client:
        await design_page()
        review_input = _input_named(design_client, "review")

        assert "k-input" in _element_classes(review_input)
        assert "k-control-shell" in _element_classes(_parent_element(review_input))
        assert "k-input-shell" in _element_classes(_parent_element(review_input))


async def test_library_tag_filter_reports_katalog_failure_without_losing_active_tags(
    monkeypatch: MonkeyPatch,
) -> None:
    class UnavailableCatalogue:
        def __init__(self, _settings: Kanvas_Settings, _user_id: int | None = None) -> None:
            pass

        async def library_tags(self) -> tuple[str, ...]:
            raise KatalogClientError(KatalogClientErrorKind.UNAVAILABLE, "offline")

    monkeypatch.setattr(library_route, "KanvasKatalogService", UnavailableCatalogue)
    with Client(page("")) as client:
        await render_library(
            Kanvas_Settings(), _selected_profile(), LibraryFilters(tags=("anime",))
        )
        tags = _select_named(client, "tag")
        feedback_titles = [
            element
            for element in client.elements.values()
            if "k-feedback__title" in _element_classes(element)
        ]

    assert _element_props(tags)["multiple"] is True
    assert len(feedback_titles) == 1


def test_poster_component_passes_one_safe_payload_to_the_browser_renderer() -> None:
    poster = PosterView(
        id=7,
        title='Poster "title"',
        href="/item/7",
        progressPercent=25,
        available=True,
    )

    with Client(page("")) as client:
        poster_card(poster)
        element = next(
            element for element in client.elements.values() if element.tag == "kanvas-poster"
        )

    payload = json.loads(cast(str, _element_props(element)["poster"]))
    assert payload == poster.model_dump(by_alias=True, mode="json")


def _input_named(client: Client, name: str) -> Element:
    return next(
        element
        for element in client.elements.values()
        if element.tag == "input" and _element_props(element).get("name") == name
    )


def _select_named(client: Client, name: str) -> Element:
    return next(
        element
        for element in client.elements.values()
        if element.tag == "select" and _element_props(element).get("name") == name
    )


def _parent_element(element: Element) -> Element:
    assert element.parent_slot is not None
    return element.parent_slot.parent


def _element_classes(element: Element) -> list[str]:
    """Expose NiceGUI's internal test-only rendered class list."""

    return element._classes  # pyright: ignore[reportPrivateUsage]


def _element_props(element: Element) -> dict[str, object]:
    """Expose NiceGUI's internal test-only rendered attributes."""

    return cast(dict[str, object], element._props)  # pyright: ignore[reportPrivateUsage]


def test_profile_controls_do_not_duplicate_the_administration_navigation() -> None:
    owner = _selected_profile()
    member = SessionProfile(UserSummary(id=2, username="member", role=UserRole.USER))

    with Client(page("")) as owner_client:
        primary_navigation("/library", owner)
        owner_shortcuts = [
            element
            for element in owner_client.elements.values()
            if "k-administration-shortcut" in _element_classes(element)
        ]

    with Client(page("")) as member_client:
        primary_navigation("/library", member)
        member_shortcuts = [
            element
            for element in member_client.elements.values()
            if "k-administration-shortcut" in _element_classes(element)
        ]

    assert owner_shortcuts == []
    assert member_shortcuts == []


def test_asset_versions_are_deterministic_content_addresses(tmp_path: Path) -> None:
    css_path = tmp_path / "kanvas.css"
    javascript_path = tmp_path / "kanvas.js"
    css_path.write_text(".k-app { color: white; }", encoding="utf-8")
    javascript_path.write_text("window.kanvas = {};", encoding="utf-8")

    initial_versions = kanvas_asset_versions(tmp_path)
    head = kanvas_head_html(initial_versions)
    assert initial_versions == kanvas_asset_versions(tmp_path)
    assert f"/_kanvas/kanvas.css?v={initial_versions.css}" in head
    assert f"/_kanvas/kanvas.js?v={initial_versions.javascript}" in head

    css_path.write_text(".k-app { color: black; }", encoding="utf-8")

    assert kanvas_asset_versions(tmp_path).css != initial_versions.css
    assert kanvas_asset_versions(tmp_path).javascript == initial_versions.javascript


def test_development_mode_disables_static_asset_caching() -> None:
    assert Kanvas_Settings().static_max_cache_age == 3600
    assert Kanvas_Settings(development_mode=True).static_max_cache_age == 0


def test_console_main_uses_auto_browser_open_setting(monkeypatch: MonkeyPatch) -> None:
    run_options: list[dict[str, object]] = []

    def fake_run(**kwargs: object) -> None:
        run_options.append(kwargs)

    def fake_main() -> None:
        pass

    def fake_build_dashboard(_settings: Kanvas_Settings) -> None:
        pass

    monkeypatch.setattr(kanvas_main, "main", fake_main)
    monkeypatch.setattr(kanvas_main, "build_dashboard", fake_build_dashboard)
    monkeypatch.setattr(kanvas_main.ui, "run", fake_run)

    kanvas_main.console_main()

    monkeypatch.setenv("KASANA_KANVAS_AUTO_BROWSER_OPEN", "true")
    kanvas_main.console_main()

    assert [options["show"] for options in run_options] == [False, True]


async def test_service_transforms_real_public_contracts_through_one_fake_client(
    monkeypatch: MonkeyPatch,
) -> None:
    artwork = ArtworkSelection(
        id=8,
        kind=ArtworkKind.POSTER,
        url="/api/v1/library/items/7/artwork/8",
        content_type="image/jpeg",
        size_bytes=4,
    )
    item = _item(artwork=(artwork,))

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_arguments: object) -> None:
            pass

        async def continue_watching(
            self, _user_id: int, *, cursor: str | None = None, limit: int = 50
        ) -> PaginatedResponse[object]:
            return PaginatedResponse(items=(), next_cursor=None, limit=limit)

        async def on_deck(
            self, _user_id: int, *, cursor: str | None = None, limit: int = 50
        ) -> PaginatedResponse[object]:
            return PaginatedResponse(items=(), next_cursor=None, limit=limit)

        async def list_library_items(
            self, **_arguments: object
        ) -> PaginatedResponse[LibraryItemSummary]:
            return PaginatedResponse(items=(item,), next_cursor="next", limit=48)

        async def recently_added_catalogue_items(
            self, *, limit: int = 20
        ) -> PaginatedResponse[LibraryItemSummary]:
            return PaginatedResponse(items=(item,), next_cursor=None, limit=limit)

    def fake_client(*_args: object, **_kwargs: object) -> FakeClient:
        return FakeClient()

    monkeypatch.setattr("kasana.kanvas.services.katalog.KatalogClient", fake_client)
    service = KanvasKatalogService(Kanvas_Settings(), _selected_profile().user.id)

    rails = await service.home_rails()
    posters, next_cursor = await service.library_page(LibraryFilters(tags=("anime",)), cursor=None)

    assert [rail.title for rail in rails] == ["Continue", "On Deck", "Recently Added"]
    assert posters[0].poster_url == "/kanvas/artwork/7/8"
    assert next_cursor == "next"


async def test_item_picker_uses_a_bounded_server_side_library_search(
    monkeypatch: MonkeyPatch,
) -> None:
    item = _item()

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_arguments: object) -> None:
            pass

        async def iter_collection_members(
            self, collection_id: int, *, limit: int
        ) -> AsyncIterator[CollectionMembership]:
            assert collection_id == 4
            assert limit == 100
            yield CollectionMembership(id=1, collection_id=4, item=item)

        async def list_library_items(
            self, *, cursor: str | None, limit: int, search: str | None
        ) -> PaginatedResponse[LibraryItemSummary]:
            assert cursor == "next"
            assert limit == 48
            assert search == "Stargate"
            return PaginatedResponse(items=(item,), next_cursor=None, limit=limit)

    def fake_client(*_args: object, **_kwargs: object) -> FakeClient:
        return FakeClient()

    monkeypatch.setattr("kasana.kanvas.services.katalog.KatalogClient", fake_client)

    items, next_cursor = await KanvasKatalogService(Kanvas_Settings()).item_picker_page(
        4,
        cursor="next",
        search="Stargate",
        playable_only=False,
    )

    assert next_cursor is None
    assert items[0].already_member is True
    assert items[0].poster_url is None


def test_routes_assets_keyboard_and_reduced_motion_contracts() -> None:
    build_dashboard()
    paths = {route.path for route in app.routes if isinstance(route, Route)}

    assert {
        "/",
        "/library",
        "/item/{item_id}",
        "/collections",
        "/collections/new",
        "/collections/{collection_id}",
        "/collections/{collection_id}/edit",
        "/collections/{collection_id}/watch-orders/new",
        "/watch-orders/{watch_order_id}",
        "/watch-orders/{watch_order_id}/edit",
        "/administration",
        "/administration/metadata",
        "/administration/libraries",
        "/administration/jobs",
        "/administration/artwork",
        "/administration/hierarchy",
        "/_design",
    } <= paths
    assert "/search" not in paths
    assert keyboard_action("Enter") is NavigationAction.ACTIVATE
    assert keyboard_action("Escape") is NavigationAction.BACK
    assert keyboard_action("/") is NavigationAction.FOCUS_SEARCH
    assert keyboard_action("Unknown") is None
    static_root = Path(__file__).parents[1] / "src" / "kasana" / "kanvas" / "static"
    css = (static_root / "kanvas.css").read_text()
    javascript = (static_root / "kanvas.js").read_text()
    assert "prefers-reduced-motion: reduce" in css
    assert ".k-control-shell" in css
    assert ".k-input-shell" in css
    assert ".k-input--review" not in css
    assert ".k-input-reveal" not in css
    assert ".k-input:focus-visible { outline: none; }" in css
    assert ".k-select:focus-visible { outline: none; }" in css
    assert ".k-check input:focus-visible { outline: none; }" in css
    assert ".k-check-menu__summary" in css
    assert ".k-check-menu__option" in css
    assert ".k-control-shell:focus-within::after" in css
    assert ".k-admin-root-form .k-select-wrap" in css
    assert ".k-admin-root-form .k-check { width: fit-content; }" in css
    assert ".k-admin-root-path-row" in css
    assert ".k-directory-picker__entry" in css
    assert "background-size: 100% 1px, 1px 100%, 100% 1px, 1px 100%" in css
    assert "IntersectionObserver" in javascript
    assert "MAX_MOUNTED_POSTERS" in javascript
    assert "kanvas-collection-grid" in javascript
    assert "kanvas-item-picker" in javascript
    assert "kanvas-watch-order-list" in javascript
    assert "dragstart" in javascript
    assert "onDrop" in javascript
    assert "moveBoundary" in javascript
    assert "showConflict" in javascript
    assert "currentRevision" in javascript
    assert 'data-row-action="play"' in javascript
    assert "<dialog" in javascript
    assert "kanvas-poster" in javascript
    assert "posterMarkup" in javascript
    assert "sessionStorage" in javascript
    assert "LIBRARY_GRID_SCHEMA_VERSION" in javascript
    assert "libraryGridPayload" in javascript
    assert "AbortController" in javascript
    assert "k-picker k-admin-root-form" in javascript
    assert 'name="path" value="${escapeHtml(root?.path || \'\')}"' in javascript
    assert "directories-source" in javascript
    assert "data-admin-root-browse" in javascript
    assert "browseRootDirectory" in javascript
    assert "normalisedGridSource" in javascript
    assert "kanvas-onboarding" in javascript
    assert "const jobDetail" in javascript
    assert "job.failure || job.message" in javascript
    assert "const providerEntryUrl" in javascript
    assert "https://www.themoviedb.org/${section}/${providerId}" in javascript
    assert "https://www.imdb.com/title/${providerId}/" in javascript
    assert "https://www.tvmaze.com/shows/${providerId}" in javascript
    assert "k-metadata-selected__title" in css


def test_administration_overview_transformation_and_adaptive_polling() -> None:
    overview = overview_from_status(
        StatusResponse(
            database_revision="20260719_0010",
            item_count=4,
            media_file_count=5,
            available_file_count=4,
            unresolved_audit_issue_count=2,
            active_job_count=1,
            failed_job_count=2,
            interrupted_job_count=3,
            artwork_cache_size_bytes=123,
            artwork_cache_file_count=2,
        ),
        unavailable_root_count=1,
        unresolved_metadata_count=2,
    )

    assert overview.connected is True
    assert overview.unavailable_root_count == 1
    assert overview.unresolved_metadata_count == 2
    state = AdaptivePollingState()
    assert state.begin() is True
    assert state.begin() is False
    state.finish()
    assert state.interval_seconds(active_jobs=1) == 5
    assert state.interval_seconds(active_jobs=0) == 30
    state.hidden = True
    assert state.begin() is False
    assert state.interval_seconds(active_jobs=1) is None

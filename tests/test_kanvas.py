"""Behaviour contracts for the first Kanvas visual foundation."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from _pytest.monkeypatch import MonkeyPatch
from nicegui import app
from nicegui.client import Client
from nicegui.page import page
from starlette.requests import Request
from starlette.routing import Route

from kasana.kanvas.components.controls import NavigationAction, keyboard_action
from kasana.kanvas.components.feedback import feedback_state, skeleton_posters
from kasana.kanvas.components.media_rail import media_rail
from kasana.kanvas.components.navigation import primary_navigation
from kasana.kanvas.components.poster import poster_card
from kasana.kanvas.components.progress import progress_indicator
from kasana.kanvas.components.shell import page_shell
from kasana.kanvas.dashboard import (
    administration_page,
    build_dashboard,
    collections_page,
    design_page,
    library_data,
    search_page,
)
from kasana.kanvas.routes import home as home_route
from kasana.kanvas.routes import item as item_route
from kasana.kanvas.routes.library import render_library
from kasana.kanvas.services.katalog import KanvasKatalogService, poster_from_summary, poster_state
from kasana.kanvas.services.playback import (
    OptimisticWatchedState,
    launch_uri,
    playback_plan_request,
)
from kasana.kanvas.settings import Kanvas_Settings
from kasana.kanvas.viewmodels.home import MediaRailView
from kasana.kanvas.viewmodels.item import ItemDetailView
from kasana.kanvas.viewmodels.library import CursorPager, LibraryFilters, PosterState, PosterView
from kasana.katalog.public import (
    ArtworkKind,
    ArtworkSelection,
    Availability,
    KatalogClientError,
    KatalogClientErrorKind,
    LibraryItemDetail,
    LibraryItemKind,
    LibraryItemSummary,
    PaginatedResponse,
    PlaybackStateResponse,
    SeriesPlaybackContext,
    StandalonePlaybackContext,
    WatchedFilter,
)


def _item(*, artwork: tuple[ArtworkSelection, ...] = ()) -> LibraryItemSummary:
    return LibraryItemSummary(
        id=7,
        title="A title",
        kind=LibraryItemKind.MOVIE,
        year=2004,
        availability=Availability.AVAILABLE,
        artwork=artwork,
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
            "anime": "1",
            "watched": "in_progress",
            "availability": "available",
            "year": "2001",
        }
    )

    assert filters.to_katalog_arguments() == {
        "kind": LibraryItemKind.MOVIE,
        "tags": ("anime",),
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
    assert b"Katalog could not load the library" in response.body


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
    request = Request({"type": "http", "query_string": b"cursor=later", "headers": []})

    response = await library_data(request)

    assert response.status_code == 200
    payload = json.loads(bytes(response.body))
    assert payload["nextCursor"] is None
    assert payload["items"][0]["posterUrl"] == "/kanvas/artwork/7/8"
    assert "playback_url" not in json.dumps(payload)


def test_native_component_builders_cover_poster_rail_feedback_and_shell() -> None:
    settings = Kanvas_Settings()
    poster = PosterView(id=7, title="Poster", href="/item/7", available=True)

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


def test_native_icon_builder_rejects_unknown_icon() -> None:
    from kasana.kanvas.components.controls import icon_svg

    with pytest.raises(ValueError, match="Unknown Kanvas icon"):
        icon_svg("not-an-icon")


async def test_visual_routes_render_with_fake_katalog_data(monkeypatch: MonkeyPatch) -> None:
    poster = PosterView(id=7, title="Poster", href="/item/7", available=True)

    class HomeCatalog:
        def __init__(self, _settings: Kanvas_Settings) -> None:
            pass

        async def home_rails(self) -> tuple[MediaRailView, ...]:
            return (MediaRailView(title="Continue", posters=(poster,)),)

    class ItemCatalog:
        def __init__(self, _settings: Kanvas_Settings) -> None:
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

    with Client(page("")):
        monkeypatch.setattr(home_route, "KanvasKatalogService", HomeCatalog)
        await home_route.render_home(Kanvas_Settings())
        monkeypatch.setattr(item_route, "KanvasKatalogService", ItemCatalog)
        await item_route.render_item(Kanvas_Settings(), 7)
        render_library(Kanvas_Settings(), LibraryFilters(search="poster"))
        await collections_page()
        await search_page()
        await administration_page()
        await design_page()


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

    def fake_client(*_args: object, **_kwargs: object) -> FakeClient:
        return FakeClient()

    monkeypatch.setattr("kasana.kanvas.services.katalog.KatalogClient", fake_client)
    service = KanvasKatalogService(Kanvas_Settings())

    rails = await service.home_rails()
    posters, next_cursor = await service.library_page(LibraryFilters(anime=True), cursor=None)

    assert [rail.title for rail in rails] == ["Continue", "On Deck", "Added"]
    assert posters[0].poster_url == "/kanvas/artwork/7/8"
    assert next_cursor == "next"


def test_routes_assets_keyboard_and_reduced_motion_contracts() -> None:
    build_dashboard()
    paths = {route.path for route in app.routes if isinstance(route, Route)}

    assert {
        "/",
        "/library",
        "/item/{item_id}",
        "/collections",
        "/search",
        "/administration",
        "/_design",
    } <= paths
    assert keyboard_action("Enter") is NavigationAction.ACTIVATE
    assert keyboard_action("Escape") is NavigationAction.BACK
    assert keyboard_action("/") is NavigationAction.FOCUS_SEARCH
    assert keyboard_action("Unknown") is None
    static_root = Path(__file__).parents[1] / "src" / "kasana" / "kanvas" / "static"
    css = (static_root / "kanvas.css").read_text()
    javascript = (static_root / "kanvas.js").read_text()
    assert "prefers-reduced-motion: reduce" in css
    assert "IntersectionObserver" in javascript
    assert "MAX_MOUNTED_POSTERS" in javascript
    assert "sessionStorage" in javascript

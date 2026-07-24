"""Contract coverage for Katalog's versioned HTTP boundary."""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from kasana.katalog.api.app import create_app
from kasana.katalog.api.runtime import KatalogApiRuntime
from kasana.katalog.client import KatalogClient, KatalogClientError, KatalogClientErrorKind
from kasana.katalog.database import KatalogDatabase
from kasana.katalog.metadata import MetadataProvider, MetadataWorkflow, SearchOutcome
from kasana.katalog.models import (
    AvailabilityState,
    CachedArtwork,
    CachedArtworkKind,
    KeiroKind,
    MetadataCandidate,
    MetadataCandidateStatus,
    User,
    Zaisan,
    ZaisanKind,
)
from kasana.katalog.public import JobStatus, UserAuthentication, UserCreate, UserUpdate
from kasana.katalog.repair import HierarchyRepairPlan, HierarchyRepairResult, RepairImpact
from kasana.katalog.scanning import ScanResult, ScanTotals
from kasana.katalog.services import (
    add_collection_membership,
    append_watch_order_entry,
    attach_media_file,
    create_collection,
    create_library_item,
    create_library_root,
    create_user,
    create_watch_order,
    record_playback_progress,
)
from kasana.katalog.settings import KatalogSettings
from kasana.shared.profile_rules import PROFILE_ACCENT_COLOUR_DEFAULT


@dataclass(frozen=True)
class ApiFixture:
    client: httpx.AsyncClient
    runtime: KatalogApiRuntime
    settings: KatalogSettings
    database: KatalogDatabase


@pytest.fixture
async def api_fixture(tmp_path: Path) -> AsyncIterator[ApiFixture]:
    database_path = tmp_path / "katalog.sqlite3"
    artwork_path = tmp_path / "artwork"
    library_path = tmp_path / "library"
    library_path.mkdir()
    artwork_path.mkdir()
    (artwork_path / "poster.jpg").write_bytes(b"poster")
    (library_path / "alpha [Extended].mkv").write_bytes(b"seeded media")
    database = KatalogDatabase(database_path)
    database.create_schema()
    with database.transaction() as session:
        root = create_library_root(
            session,
            path=library_path,
            expected_media_kind=ZaisanKind.MOVIE,
            default_tags=frozenset({"genre", "favourite"}),
            display_name="Movies",
        )
        alpha = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="Alpha",
            sort_title="Alpha",
            release_year=2001,
        )
        beta = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="Beta",
            sort_title="Beta",
            release_year=2002,
        )
        create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="Gamma",
            sort_title="Gamma",
            release_year=2003,
            availability=AvailabilityState.MISSING,
        )
        attach_media_file(
            session,
            library_item_id=alpha.id,
            absolute_path=library_path / "alpha [Extended].mkv",
            size_bytes=123,
            mtime_ns=456,
            container="matroska,webm",
            duration_seconds=120.0,
            video_streams=({"codec_name": "h264", "width": 1920, "height": 1080},),
            audio_streams=({"codec_name": "aac", "channels": 2},),
        )
        collection = create_collection(session, name="Letters")
        add_collection_membership(session, collection_id=collection.id, library_item_id=alpha.id)
        order = create_watch_order(
            session, collection_id=collection.id, name="Release", order_kind=KeiroKind.AIR
        )
        append_watch_order_entry(session, watch_order_id=order.id, library_item_id=alpha.id)
        append_watch_order_entry(session, watch_order_id=order.id, library_item_id=beta.id)
        user = create_user(session, username="tester")
        record_playback_progress(
            session,
            user_id=user.id,
            library_item_id=beta.id,
            position_seconds=10.0,
            duration_seconds=100.0,
            completed=False,
        )
        record_playback_progress(
            session,
            user_id=user.id,
            library_item_id=alpha.id,
            position_seconds=120.0,
            duration_seconds=120.0,
            completed=True,
        )
        session.add(
            CachedArtwork(
                library_item_id=alpha.id,
                provider="fixture",
                provider_id="alpha",
                artwork_kind=CachedArtworkKind.POSTER,
                provider_revision="1",
                source_url="https://example.test/poster.jpg",
                attribution=None,
                content_type="image/jpeg",
                cache_relative_path="poster.jpg",
                size_bytes=6,
                downloaded_at=datetime.now(UTC),
            )
        )
        session.add(
            MetadataCandidate(
                library_item_id=alpha.id,
                provider="fixture",
                provider_id="alpha",
                provider_media_kind=ZaisanKind.MOVIE,
                provider_title="Alpha",
                provider_original_title=None,
                provider_release_year=2001,
                provider_original_language="en",
                poster_source_url=None,
                poster_revision=None,
                confidence=0.8,
                scoring_explanation=[],
                status=MetadataCandidateStatus.SUGGESTED,
                last_seen_at=datetime.now(UTC),
                rejected_at=None,
            )
        )
    settings = KatalogSettings(
        database_path=database_path,
        artwork_cache_path=artwork_path,
        user_configuration_directory=tmp_path / "users",
    )
    app = create_app(settings, database=database)
    runtime = KatalogApiRuntime(settings, database)
    app.state.runtime = runtime
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://katalog.test") as client:
        yield ApiFixture(client, runtime, settings, database)
    await runtime.close()
    database.close()


async def test_library_pagination_is_stable_and_filters_are_server_side(
    api_fixture: ApiFixture,
) -> None:
    first = await api_fixture.client.get(
        "/api/v1/library/items", params={"limit": 2, "tag": "genre"}
    )

    assert first.status_code == 200
    first_payload = first.json()
    assert [item["title"] for item in first_payload["items"]] == ["Alpha", "Beta"]
    assert first_payload["items"][0]["context_label"] == "Extended"
    assert first_payload["next_cursor"]

    second = await api_fixture.client.get(
        "/api/v1/library/items",
        params={"limit": 2, "cursor": first_payload["next_cursor"]},
    )
    assert [item["title"] for item in second.json()["items"]] == ["Gamma"]
    assert second.json()["next_cursor"] is None

    assert (await api_fixture.client.get("/api/v1/library/items", params={"year": 2002})).json()[
        "items"
    ][0]["title"] == "Beta"
    assert (await api_fixture.client.get("/api/v1/library/items", params={"search": "alp"})).json()[
        "items"
    ][0]["title"] == "Alpha"
    assert (
        await api_fixture.client.get("/api/v1/library/items", params={"availability": "missing"})
    ).json()["items"][0]["title"] == "Gamma"
    assert (
        await api_fixture.client.get("/api/v1/library/items", params={"collection_id": 1})
    ).json()["items"][0]["title"] == "Alpha"
    assert (
        await api_fixture.client.get(
            "/api/v1/library/items", params={"watched": "watched", "user_id": 1}
        )
    ).json()["items"][0]["title"] == "Alpha"
    assert [
        item["title"]
        for item in (
            await api_fixture.client.get("/api/v1/library/items", params={"tag": "movies"})
        ).json()["items"]
    ] == ["Alpha", "Beta", "Gamma"]


async def test_missing_library_root_is_status_only_and_recovers(api_fixture: ApiFixture) -> None:
    missing_path = api_fixture.settings.database_path.parent / "offline-root"
    created = await api_fixture.client.post(
        "/api/v1/library/roots",
        json={
            "path": str(missing_path),
            "expected_kind": "movie",
            "enabled": True,
        },
    )

    assert created.status_code == 201
    created_root = created.json()
    assert created_root["available"] is False

    status = (await api_fixture.client.get("/api/v1/status")).json()
    assert status["enabled_root_count"] == 2
    assert status["unavailable_root_count"] == 1
    roots = (await api_fixture.client.get("/api/v1/library/roots")).json()
    assert {root["path"]: root["available"] for root in roots}[str(missing_path)] is False

    job = await api_fixture.runtime.submit_scan(
        root_id=created_root["id"], include_unavailable=False, dry_run=False
    )
    await api_fixture.runtime.jobs._tasks[job.id]  # pyright: ignore[reportPrivateUsage]
    completed = await api_fixture.runtime.jobs.get(job.id)
    assert completed.status is JobStatus.COMPLETED
    assert completed.result_counters["failed"] == 1
    assert completed.message is not None
    assert "scan issue" in completed.message

    missing_path.mkdir()
    recovered_status = (await api_fixture.client.get("/api/v1/status")).json()
    assert recovered_status["unavailable_root_count"] == 0
    recovered_roots = (await api_fixture.client.get("/api/v1/library/roots")).json()
    assert {root["path"]: root["available"] for root in recovered_roots}[str(missing_path)] is True


async def test_library_summaries_include_safe_context_labels(api_fixture: ApiFixture) -> None:
    created_ids: dict[str, int] = {}
    with api_fixture.database.transaction() as session:
        series = create_library_item(
            session,
            library_root_id=1,
            item_kind=ZaisanKind.SERIES,
            title="Context Show",
        )
        season = create_library_item(
            session,
            library_root_id=1,
            parent_id=series.id,
            item_kind=ZaisanKind.SEASON,
            title="Season 1",
            season_number=1,
        )
        episode = create_library_item(
            session,
            library_root_id=1,
            parent_id=season.id,
            item_kind=ZaisanKind.EPISODE,
            title="Context Show",
            season_number=1,
            episode_number=3,
        )
        special = create_library_item(
            session,
            library_root_id=1,
            parent_id=series.id,
            item_kind=ZaisanKind.SPECIAL,
            title="Special Two",
            season_number=0,
        )
        extra = create_library_item(
            session,
            library_root_id=1,
            parent_id=series.id,
            item_kind=ZaisanKind.EXTRA,
            title="Interview",
        )
        for item, relative_path in (
            (episode, Path("Context Show") / "Season 01" / "Context Show S01E03.mkv"),
            (special, Path("Context Show") / "Season 00" / "Context Show S00E02.mkv"),
            (extra, Path("Context Show") / "Season 01" / "Extras" / "Interview X02.mkv"),
        ):
            attach_media_file(
                session,
                library_item_id=item.id,
                absolute_path=api_fixture.settings.database_path.parent / relative_path,
                size_bytes=123,
                mtime_ns=456,
                container="matroska,webm",
            )
        created_ids = {
            "episode": episode.id,
            "special": special.id,
            "extra": extra.id,
        }

    response = await api_fixture.client.get("/api/v1/library/items", params={"limit": 20})
    items = response.json()["items"]
    by_id = {item["id"]: item for item in items}
    episode_item = by_id[created_ids["episode"]]
    special_item = by_id[created_ids["special"]]
    extra_item = by_id[created_ids["extra"]]

    assert episode_item["series_title"] == "Context Show"
    assert episode_item["context_label"] == "S01 E03"
    assert special_item["context_label"] == "S00 E02"
    assert extra_item["context_label"] == "S01 X02"
    assert str(api_fixture.settings.database_path.parent) not in json.dumps(response.json())


async def test_library_item_edit_is_audited_and_never_changes_media_files(
    api_fixture: ApiFixture,
) -> None:
    def stored_media_path() -> str:
        def load(session: Session) -> str:
            item = session.get(Zaisan, 1)
            assert item is not None
            assert item.media_files
            return item.media_files[0].absolute_path

        return api_fixture.database.run_transaction(load)

    media_path = stored_media_path()
    response = await api_fixture.client.patch(
        "/api/v1/library/items/1",
        json={
            "actor": "owner",
            "title": "Edited Alpha",
            "sort_title": "Alpha, Edited",
            "overview": "Local overview",
            "release_date": "2001-03-04",
            "release_year": 2001,
            "tags": ["anime", "favourite"],
            "locked_metadata_fields": ["title", "overview"],
            "selected_artwork": [{"kind": "poster", "artwork_id": 1}],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["item"]["title"] == "Edited Alpha"
    assert payload["item"]["tags"] == ["anime", "favourite", "genre", "movies"]
    assert payload["item"]["selected_artwork"] == [{"kind": "poster", "artwork_id": 1}]
    assert set(payload["audit"]["changed_fields"]) >= {
        "title",
        "sort_title",
        "overview",
        "release_date",
        "tags",
    }
    assert (await api_fixture.client.get("/api/v1/library/tags")).json() == [
        "anime",
        "favourite",
        "genre",
        "movies",
    ]
    audit = await api_fixture.client.get("/api/v1/library/items/1/edit-audit")
    assert audit.status_code == 200
    assert audit.json()[0]["actor"] == "owner"
    current_media_path = stored_media_path()
    assert current_media_path == media_path

    clear_artwork = await api_fixture.client.patch(
        "/api/v1/library/items/1",
        json={"actor": "owner", "selected_artwork": []},
    )
    assert clear_artwork.status_code == 200
    assert clear_artwork.json()["item"]["selected_artwork"] == []
    assert clear_artwork.json()["audit"]["changed_fields"] == ["selected_artwork"]

    invalid_hierarchy = await api_fixture.client.patch(
        "/api/v1/library/items/1",
        json={"actor": "owner", "kind": "series"},
    )
    assert invalid_hierarchy.status_code == 422

    hierarchy_change = await api_fixture.client.patch(
        "/api/v1/library/items/2",
        json={"actor": "owner", "kind": "extra", "parent_id": 1},
    )
    assert hierarchy_change.status_code == 200
    hierarchy_item = hierarchy_change.json()["item"]
    assert hierarchy_item["kind"] == "extra"
    assert hierarchy_item["parent_id"] == 1


async def test_recently_added_coalesces_new_series_activity_and_excludes_unavailable_items(
    api_fixture: ApiFixture,
) -> None:
    """A rail represents catalogue identities, rather than a batch of episode files."""

    with api_fixture.database.transaction() as session:
        alpha = session.get(Zaisan, 1)
        beta = session.get(Zaisan, 2)
        assert alpha is not None
        assert beta is not None
        alpha.added_at = datetime(2025, 1, 1, tzinfo=UTC)
        beta.added_at = datetime(2025, 1, 2, tzinfo=UTC)
        series = create_library_item(
            session,
            library_root_id=1,
            item_kind=ZaisanKind.SERIES,
            title="New Series",
        )
        season = create_library_item(
            session,
            library_root_id=1,
            parent_id=series.id,
            item_kind=ZaisanKind.SEASON,
            title="Season 1",
            season_number=1,
        )
        first_episode = create_library_item(
            session,
            library_root_id=1,
            parent_id=season.id,
            item_kind=ZaisanKind.EPISODE,
            title="Episode 1",
            season_number=1,
            episode_number=1,
        )
        second_episode = create_library_item(
            session,
            library_root_id=1,
            parent_id=season.id,
            item_kind=ZaisanKind.EPISODE,
            title="Episode 2",
            season_number=1,
            episode_number=2,
        )
        special = create_library_item(
            session,
            library_root_id=1,
            parent_id=series.id,
            item_kind=ZaisanKind.SPECIAL,
            title="Bonus",
        )
        new_movie = create_library_item(
            session,
            library_root_id=1,
            item_kind=ZaisanKind.MOVIE,
            title="New Movie",
        )
        unavailable = create_library_item(
            session,
            library_root_id=1,
            item_kind=ZaisanKind.MOVIE,
            title="Unavailable Movie",
            availability=AvailabilityState.UNAVAILABLE,
        )
        series.added_at = datetime(2026, 7, 10, tzinfo=UTC)
        first_episode.added_at = datetime(2026, 7, 15, tzinfo=UTC)
        second_episode.added_at = datetime(2026, 7, 16, tzinfo=UTC)
        special.added_at = datetime(2026, 7, 17, tzinfo=UTC)
        new_movie.added_at = datetime(2026, 7, 14, tzinfo=UTC)
        unavailable.added_at = datetime(2026, 7, 18, tzinfo=UTC)

    response = await api_fixture.client.get("/api/v1/library/recently-added", params={"limit": 10})

    assert response.status_code == 200
    payload = response.json()
    assert [item["title"] for item in payload["items"]] == [
        "New Series",
        "New Movie",
        "Beta",
        "Alpha",
    ]
    assert payload["next_cursor"] is None
    assert len([item for item in payload["items"] if item["title"] == "New Series"]) == 1


async def test_item_etags_routes_and_no_filesystem_paths_leak(
    api_fixture: ApiFixture, tmp_path: Path
) -> None:
    response = await api_fixture.client.get("/api/v1/library/items/1")

    assert response.status_code == 200
    assert response.headers["etag"]
    assert response.json()["playback_url"] == "/api/v1/playback/items/1"
    assert str(tmp_path) not in json.dumps(response.json())

    unchanged = await api_fixture.client.get(
        "/api/v1/library/items/1", headers={"If-None-Match": response.headers["etag"]}
    )
    assert unchanged.status_code == 304

    media = (await api_fixture.client.get("/api/v1/library/items/1/media")).json()
    assert media["items"][0]["container"] == "matroska"
    assert str(tmp_path) not in json.dumps(media)

    artwork = (await api_fixture.client.get("/api/v1/library/items/1/artwork")).json()[0]
    assert artwork["url"] == "/api/v1/library/items/1/artwork/1"
    image = await api_fixture.client.get(artwork["url"])
    assert image.content == b"poster"
    assert str(tmp_path) not in image.text
    assert (
        await api_fixture.client.get(
            artwork["url"], headers={"If-None-Match": image.headers["etag"]}
        )
    ).status_code == 304


async def test_route_contracts_and_mutations(api_fixture: ApiFixture) -> None:
    expected_gets = (
        "/api/v1/health",
        "/api/v1/status",
        "/api/v1/users",
        "/api/v1/library/items",
        "/api/v1/library/recently-added",
        "/api/v1/library/items/1/children",
        "/api/v1/library/items/1/media",
        "/api/v1/library/items/1/artwork",
        "/api/v1/collections",
        "/api/v1/collections/1",
        "/api/v1/collections/1/watch-orders",
        "/api/v1/watch-orders/1",
        "/api/v1/users/1/continue-watching",
        "/api/v1/users/1/on-deck",
        "/api/v1/metadata/review",
        "/api/v1/jobs",
        "/api/v1/repairs/hierarchy/preview",
    )
    for path in expected_gets:
        assert (await api_fixture.client.get(path)).status_code == 200

    progress = await api_fixture.client.put(
        "/api/v1/users/1/items/1/progress",
        json={"position_seconds": 20, "duration_seconds": 120, "completed": False},
    )
    assert progress.status_code == 200
    assert progress.json()["position_seconds"] == 20
    persisted_progress = await api_fixture.client.get("/api/v1/users/1/items/1/progress")
    assert persisted_progress.status_code == 200
    assert persisted_progress.json()["completed"] is False
    assert (await api_fixture.client.post("/api/v1/users/1/items/1/watched")).json()[
        "completed"
    ] is True
    persisted_watched = await api_fixture.client.get("/api/v1/users/1/items/1/progress")
    assert persisted_watched.status_code == 200
    assert persisted_watched.json()["completed"] is True
    assert (await api_fixture.client.delete("/api/v1/users/1/items/1/watched")).status_code == 204
    cleared_progress = await api_fixture.client.get("/api/v1/users/1/items/1/progress")
    assert cleared_progress.status_code == 200
    assert cleared_progress.json() is None
    assert (
        await api_fixture.client.post(
            "/api/v1/metadata/items/1/reject",
            json={"provider": "fixture", "provider_id": "alpha"},
        )
    ).status_code == 200
    assert (await api_fixture.client.post("/api/v1/metadata/items/1/ignore")).status_code == 200
    assert (
        await api_fixture.client.post("/api/v1/scans", json={"dry_run": True})
    ).status_code == 202
    assert (await api_fixture.client.post("/api/v1/artwork/fetch", json={})).status_code == 202
    assert (await api_fixture.client.post("/api/v1/repairs/hierarchy", json={})).status_code == 202
    assert (
        await api_fixture.client.post("/api/v1/repairs/hierarchy", json={"apply": True})
    ).status_code == 422


async def test_metadata_review_only_returns_unresolved_suggestions(
    api_fixture: ApiFixture,
) -> None:
    initial = await api_fixture.client.get("/api/v1/metadata/review")
    assert [candidate["status"] for candidate in initial.json()["items"]] == ["suggested"]

    ignored = await api_fixture.client.post("/api/v1/metadata/items/1/ignore")
    review = await api_fixture.client.get("/api/v1/metadata/review")

    assert ignored.status_code == 200
    assert review.json()["items"] == []


async def test_completed_scan_auto_matches_safe_candidates(
    api_fixture: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeScanner:
        def __init__(self, _database: KatalogDatabase, **_options: object) -> None:
            pass

        def scan(
            self,
            *,
            root_id: int | None,
            include_unavailable: bool,
            dry_run: bool,
        ) -> ScanResult:
            assert root_id == 1
            assert include_unavailable is False
            assert dry_run is False
            return ScanResult(totals=ScanTotals(discovered=3), findings=())

    class FakeWorkflow:
        async def auto_match(
            self, _providers: tuple[MetadataProvider, ...], *, root_id: int | None
        ) -> tuple[SearchOutcome, ...]:
            assert root_id == 1
            return (
                SearchOutcome(
                    item_id=1,
                    candidates=(),
                    auto_matched_provider="fake",
                    auto_matched_provider_id="safe-match",
                ),
            )

    async def with_fake_provider(
        operation: Callable[
            [MetadataWorkflow, tuple[MetadataProvider, ...]], Awaitable[tuple[SearchOutcome, ...]]
        ],
    ) -> tuple[SearchOutcome, ...]:
        return await operation(FakeWorkflow(), ())  # type: ignore[arg-type]

    monkeypatch.setattr("kasana.katalog.api.runtime.IncrementalScanner", FakeScanner)
    monkeypatch.setattr(api_fixture.runtime, "_with_provider", with_fake_provider)

    job = await api_fixture.runtime.submit_scan(
        root_id=1, include_unavailable=False, dry_run=False
    )
    task = api_fixture.runtime.jobs._tasks[job.id]  # pyright: ignore[reportPrivateUsage]
    await task
    completed = await api_fixture.runtime.jobs.get(job.id)

    assert completed.status.value == "completed"
    assert completed.result_counters == {
        "discovered": 3,
        "auto_matched": 1,
        "review_required": 0,
    }
    assert completed.message == "Scanned 3 files. Automatically matched 1 items; 0 require review."


async def test_seeded_library_deployment_smoke_path(
    api_fixture: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise the shipped API path from scan through administration on seeded media."""

    matched: list[tuple[int, str, str]] = []

    class FakeScanner:
        def __init__(self, _database: KatalogDatabase, **_options: object) -> None:
            pass

        def scan(
            self,
            *,
            root_id: int | None,
            include_unavailable: bool,
            dry_run: bool,
        ) -> ScanResult:
            assert (root_id, include_unavailable, dry_run) == (1, False, False)
            return ScanResult(totals=ScanTotals(discovered=3), findings=())

    class FakeWorkflow:
        async def auto_match(
            self, _providers: tuple[MetadataProvider, ...], *, root_id: int | None
        ) -> tuple[SearchOutcome, ...]:
            assert root_id == 1
            return ()

        async def match_item(
            self, item_id: int, _provider: object, provider_id: str, *, actor: str
        ) -> None:
            matched.append((item_id, provider_id, actor))

        async def fetch_posters(
            self, _providers: tuple[MetadataProvider, ...], *, root_id: int | None
        ) -> tuple[object, ...]:
            assert root_id == 1
            return (object(),)

    async def with_fake_provider(operation: Callable[..., Awaitable[object]]) -> object:
        return await operation(FakeWorkflow(), (SimpleNamespace(provider_name="fixture"),))

    monkeypatch.setattr("kasana.katalog.api.runtime.IncrementalScanner", FakeScanner)
    monkeypatch.setattr(api_fixture.runtime, "_with_provider", with_fake_provider)

    scan = await api_fixture.client.post("/api/v1/scans", json={"library_root_id": 1})
    assert scan.status_code == 202
    await api_fixture.runtime.jobs._tasks[scan.json()["job"]["id"]]  # pyright: ignore[reportPrivateUsage]

    metadata_match = await api_fixture.client.post(
        "/api/v1/metadata/items/1/match",
        json={"provider": "fixture", "provider_id": "alpha"},
    )
    assert metadata_match.status_code == 200
    assert matched == [(1, "alpha", "api")]

    artwork = await api_fixture.client.post(
        "/api/v1/artwork/fetch", json={"library_root_id": 1}
    )
    assert artwork.status_code == 202
    await api_fixture.runtime.jobs._tasks[artwork.json()["job"]["id"]]  # pyright: ignore[reportPrivateUsage]

    browse = await api_fixture.client.get("/api/v1/library/items", params={"search": "Alpha"})
    assert [item["title"] for item in browse.json()["items"]] == ["Alpha"]

    plan = await api_fixture.client.post(
        "/api/v1/playback/plans",
        json={"user_id": 1, "context": {"kind": "standalone", "item_id": 1}},
    )
    assert plan.status_code == 201
    progress = await api_fixture.client.put(
        "/api/v1/users/1/items/1/progress",
        json={"position_seconds": 30, "duration_seconds": 120, "completed": False},
    )
    assert progress.status_code == 200
    assert progress.json()["position_seconds"] == 30

    administration = await api_fixture.client.get("/api/v1/status")
    assert administration.status_code == 200
    assert (await api_fixture.client.get("/api/v1/jobs")).status_code == 200


async def test_library_consistency_job_scans_and_repairs(
    api_fixture: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    class FakeScanner:
        def __init__(self, _database: KatalogDatabase, **_options: object) -> None:
            pass

        def scan(
            self,
            *,
            root_id: int | None,
            include_unavailable: bool,
            dry_run: bool,
        ) -> ScanResult:
            calls.append("scan")
            assert root_id == 1
            assert include_unavailable is True
            assert dry_run is False
            return ScanResult(totals=ScanTotals(discovered=4, added=1, moved=2), findings=())

    class FakeRepairService:
        def __init__(self, _database: KatalogDatabase) -> None:
            pass

        def apply(
            self,
            _filters: object,
            *,
            backup_path: Path,
        ) -> HierarchyRepairResult:
            calls.append("repair")
            assert backup_path.name.startswith("katalog.sqlite3.hierarchy-repair-")
            return HierarchyRepairResult(
                runId="run-1",
                applied=True,
                backupPath=str(backup_path),
                plan=HierarchyRepairPlan(
                    actions=(),
                    manualReviews=(),
                    impact=RepairImpact(
                        playbackStates=0,
                        metadataBindings=0,
                        collectionMemberships=0,
                        watchOrderEntries=0,
                    ),
                ),
            )

    monkeypatch.setattr("kasana.katalog.api.runtime.IncrementalScanner", FakeScanner)
    monkeypatch.setattr("kasana.katalog.api.runtime.HierarchyRepairService", FakeRepairService)

    def fake_backup(_path: Path) -> None:
        calls.append("backup")

    monkeypatch.setattr(api_fixture.database, "backup_to", fake_backup)

    response = await api_fixture.client.post(
        "/api/v1/library/consistency",
        json={"library_root_id": 1, "include_unavailable": True, "dry_run": False},
    )
    job_id = response.json()["job"]["id"]
    task = api_fixture.runtime.jobs._tasks[job_id]  # pyright: ignore[reportPrivateUsage]
    await task
    completed = await api_fixture.runtime.jobs.get(job_id)

    assert response.status_code == 202
    assert calls == ["scan", "backup", "repair"]
    assert completed.status is JobStatus.COMPLETED
    assert completed.kind == "library-consistency"
    assert completed.result_counters["discovered"] == 4
    assert completed.result_counters["moved"] == 2
    assert completed.message == "Reconciled 4 files and applied 0 hierarchy actions."


async def test_failed_background_job_is_logged(
    api_fixture: ApiFixture, caplog: pytest.LogCaptureFixture
) -> None:
    async def fail_job() -> None:
        raise RuntimeError("fixture job failure")

    caplog.set_level(logging.ERROR, logger="kasana.katalog.api.jobs")
    job = await api_fixture.runtime.jobs.submit("fixture", fail_job)
    task = api_fixture.runtime.jobs._tasks[job.id]  # pyright: ignore[reportPrivateUsage]
    await task
    failed = await api_fixture.runtime.jobs.get(job.id)

    assert failed.status is JobStatus.FAILED
    assert failed.failure_message == "fixture job failure"
    assert "Katalog maintenance job failed" in caplog.text
    assert f"id={job.id}" in caplog.text
    assert "message=fixture job failure" in caplog.text


async def test_profile_user_operations_pin_and_disabled_playback(api_fixture: ApiFixture) -> None:
    created = await api_fixture.client.post(
        "/api/v1/users",
        json={"username": "profile", "role": "admin", "pin": "2468"},
    )
    assert created.status_code == 201
    user = created.json()
    assert user["role"] == "admin"
    assert user["pin_required"] is True
    assert "pin" not in user
    configuration_path = (
        api_fixture.settings.user_configuration_directory / str(user["id"]) / "configuration.json"
    )
    configuration = json.loads(configuration_path.read_text(encoding="utf-8"))
    assert set(configuration) == {
        "accent_colour",
        "level",
        "name",
        "pin",
        "preferred_audio_language",
        "preferred_subtitle_language",
        "state",
        "username",
    }
    assert configuration["accent_colour"] == PROFILE_ACCENT_COLOUR_DEFAULT
    assert configuration["level"] == "admin"
    assert configuration["name"] is None
    assert configuration["state"] == "active"
    assert configuration["username"] == "profile"
    assert configuration["pin"] == "2468"
    database_pin = api_fixture.database.run_transaction(
        lambda session: session.scalar(select(User.pin).where(User.id == user["id"]))
    )
    assert database_pin is None
    configuration["level"] = "user"
    configuration["accent_colour"] = "#336699"
    configuration_path.write_text(json.dumps(configuration), encoding="utf-8")
    refreshed_user = next(
        entry
        for entry in (await api_fixture.client.get("/api/v1/users")).json()
        if entry["id"] == user["id"]
    )
    assert refreshed_user["role"] == "user"
    assert refreshed_user["accent_colour"] == "#336699"

    configured_user_path = (
        api_fixture.settings.user_configuration_directory / "73" / "configuration.json"
    )
    configured_user_path.parent.mkdir()
    configured_user_path.write_text(
        json.dumps(
            {
                "username": "filesystem-profile",
                "name": "Filesystem profile",
                "level": "user",
                "state": "active",
                "pin": None,
            }
        ),
        encoding="utf-8",
    )
    assert next(
        entry
        for entry in (await api_fixture.client.get("/api/v1/users")).json()
        if entry["id"] == 73
    ) == {
        "id": 73,
        "username": "filesystem-profile",
        "display_name": "Filesystem profile",
        "role": "user",
        "is_disabled": False,
            "pin_required": False,
            "accent_colour": PROFILE_ACCENT_COLOUR_DEFAULT,
            "preferred_audio_language": None,
            "preferred_subtitle_language": None,
        }

    rejected_pin = await api_fixture.client.post(
        f"/api/v1/users/{user['id']}/authenticate", json={"pin": "0000"}
    )
    assert rejected_pin.status_code == 422
    assert "0000" not in rejected_pin.text
    assert (
        await api_fixture.client.patch(
            f"/api/v1/users/{user['id']}",
            json={"display_name": "Profile", "accent_colour": "#445566"},
        )
    ).json()["display_name"] == "Profile"
    saved_configuration = json.loads(configuration_path.read_text(encoding="utf-8"))
    assert saved_configuration["name"] == "Profile"
    assert saved_configuration["accent_colour"] == "#445566"
    assert (await api_fixture.client.post(f"/api/v1/users/{user['id']}/disable")).json()[
        "is_disabled"
    ] is True
    assert (
        await api_fixture.client.post(
            f"/api/v1/users/{user['id']}/authenticate", json={"pin": "2468"}
        )
    ).status_code == 422
    assert (
        await api_fixture.client.post(
            "/api/v1/playback/plans",
            json={"user_id": user["id"], "context": {"kind": "standalone", "item_id": 1}},
        )
    ).status_code == 422


async def test_errors_are_structured_and_database_errors_are_mapped(
    api_fixture: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = await api_fixture.client.get("/api/v1/library/items/999")
    assert missing.status_code == 404
    assert missing.json()["code"] == "not_found"
    assert missing.json()["request_id"] == missing.headers["x-request-id"]

    invalid = await api_fixture.client.get("/api/v1/library/items", params={"limit": 101})
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "validation_error"

    def unavailable(*_: object, **__: object) -> object:
        raise SQLAlchemyError("fixture database failure")

    monkeypatch.setattr(api_fixture.runtime.queries, "list_items", unavailable)
    failed = await api_fixture.client.get("/api/v1/library/items")
    assert failed.status_code == 503
    assert failed.json()["code"] == "service_unavailable"


async def test_openapi_uses_versioned_stable_operation_ids(api_fixture: ApiFixture) -> None:
    schema = (await api_fixture.client.get("/api/v1/openapi.json")).json()

    assert schema["openapi"].startswith("3.1")
    assert schema["paths"]["/api/v1/library/items"]["get"]["operationId"] == "v1_list_library_items"
    assert (
        schema["paths"]["/api/v1/library/recently-added"]["get"]["operationId"]
        == "v1_list_recently_added_catalogue_items"
    )
    assert schema["paths"]["/api/v1/users"]["get"]["operationId"] == "v1_list_users"
    assert (
        schema["paths"]["/api/v1/library/directories"]["get"]["operationId"]
        == "v1_browse_library_directories"
    )
    assert schema["paths"]["/api/v1/scans"]["post"]["operationId"] == "v1_submit_scan"
    assert (
        schema["paths"]["/api/v1/library/consistency"]["post"]["operationId"]
        == "v1_submit_library_consistency"
    )
    assert "APIError" in schema["components"]["schemas"]


async def test_library_directory_browser_lists_server_directories(
    api_fixture: ApiFixture, tmp_path: Path
) -> None:
    library_path = tmp_path / "library"
    (library_path / "Movies").mkdir()
    (library_path / "Series").mkdir()
    (library_path / "alpha.mkv").write_text("not a directory", encoding="utf-8")

    response = await api_fixture.client.get(
        "/api/v1/library/directories", params={"path": str(library_path)}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["path"] == str(library_path)
    assert payload["parent_path"] == str(tmp_path)
    assert [(entry["name"], entry["path"]) for entry in payload["entries"]] == [
        ("Movies", str(library_path / "Movies")),
        ("Series", str(library_path / "Series")),
    ]
    invalid = await api_fixture.client.get(
        "/api/v1/library/directories", params={"path": "relative"}
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "validation_error"


async def test_typed_aiohttp_client_round_trip_and_cancellation(
    api_fixture: ApiFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(api_fixture.settings, database=api_fixture.database)
    socket_handle = socket.socket()
    socket_handle.bind(("127.0.0.1", 0))
    socket_handle.listen()
    port = socket_handle.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False)
    )
    server_task = asyncio.create_task(server.serve(sockets=[socket_handle]))
    try:
        while not server.started:  # noqa: ASYNC110
            await asyncio.sleep(0.001)
        async with KatalogClient(f"http://127.0.0.1:{port}") as client:
            assert (await client.health()).status == "ok"
            assert (await client.browse_library_directories()).path
            assert (await client.list_users())[0].username == "tester"
            assert [item.title async for item in client.iter_library_items(limit=2)] == [
                "Alpha",
                "Beta",
                "Gamma",
            ]
            assert [
                item.title for item in (await client.recently_added_catalogue_items()).items
            ] == [
                "Beta",
                "Alpha",
            ]
            assert (await client.hierarchy_repair_preview()).actions == ()
            pin_profile = await client.create_user(UserCreate(username="client-pin", pin="2468"))
            await client.update_user(pin_profile.id, UserUpdate(display_name="Client PIN"))
            assert (
                await client.authenticate_user(pin_profile.id, UserAuthentication(pin="2468"))
            ).id == pin_profile.id
            await client.update_user(pin_profile.id, UserUpdate(pin=None))
            cleared_pin_profile = await client.authenticate_user(
                pin_profile.id, UserAuthentication(pin=None)
            )
            assert cleared_pin_profile.id == pin_profile.id
            detail = await client.get_library_item(1)
            assert detail.item is not None
            assert (await client.get_library_item(1, etag=detail.etag)).not_modified is True
            initial_state = await client.playback_state(1, 1)
            assert initial_state is not None
            assert initial_state.completed is True
            await client.clear_watched(1, 1)
            assert await client.playback_state(1, 1) is None
            await client.mark_watched(1, 1)
            watched_state = await client.playback_state(1, 1)
            assert watched_state is not None
            assert watched_state.completed is True
            with pytest.raises(KatalogClientError) as error:
                await client.get_collection(999)
            assert error.value.kind is KatalogClientErrorKind.NOT_FOUND

            def delayed_health() -> None:
                time.sleep(0.1)

            monkeypatch.setattr(app.state.runtime.queries, "health", delayed_health)
            pending = asyncio.create_task(client.health())
            await asyncio.sleep(0.01)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
    finally:
        server.should_exit = True
        await server_task
        socket_handle.close()

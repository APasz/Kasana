"""Contract coverage for Katalog's versioned HTTP boundary."""

from __future__ import annotations

import asyncio
import json
import socket
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import uvicorn
from sqlalchemy.exc import SQLAlchemyError

from kasana.katalog.api.app import create_app
from kasana.katalog.api.runtime import KatalogApiRuntime
from kasana.katalog.client import KatalogClient, KatalogClientError, KatalogClientErrorKind
from kasana.katalog.database import KatalogDatabase
from kasana.katalog.models import (
    AvailabilityState,
    CachedArtwork,
    CachedArtworkKind,
    KeiroKind,
    MetadataCandidate,
    MetadataCandidateStatus,
    Zaisan,
    ZaisanKind,
)
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
    database = KatalogDatabase(database_path)
    database.create_schema()
    with database.transaction() as session:
        root = create_library_root(
            session,
            path=library_path,
            expected_media_kind=ZaisanKind.MOVIE,
            default_tags=frozenset({"genre", "favourite"}),
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
            absolute_path=library_path / "alpha.mkv",
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
    settings = KatalogSettings(database_path=database_path, artwork_cache_path=artwork_path)
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
    assert (await api_fixture.client.post("/api/v1/users/1/items/1/watched")).json()[
        "completed"
    ] is True
    assert (await api_fixture.client.delete("/api/v1/users/1/items/1/watched")).status_code == 204
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
    assert schema["paths"]["/api/v1/scans"]["post"]["operationId"] == "v1_submit_scan"
    assert "APIError" in schema["components"]["schemas"]


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
            detail = await client.get_library_item(1)
            assert detail.item is not None
            assert (await client.get_library_item(1, etag=detail.etag)).not_modified is True
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

"""Playback planning, token, and range-transfer contracts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import httpx
import pytest
import uvicorn
from sqlalchemy import select
from starlette.types import Message, Scope

from kasana.katalog.api.app import create_app
from kasana.katalog.api.runtime import KatalogApiRuntime
from kasana.katalog.api.service import MediaTransferFile
from kasana.katalog.api.transfer import BoundedFileResponse, RangeStreamingFileTransferPolicy
from kasana.katalog.client import KatalogClient
from kasana.katalog.database import KatalogDatabase
from kasana.katalog.models import (
    AvailabilityState,
    KeiroKind,
    PlaybackLaunchToken,
    PlaybackSession,
    PlaybackState,
    ZaisanKind,
)
from kasana.katalog.public import (
    PlaybackPlanRequest,
    SessionProgressUpdate,
    StandalonePlaybackContext,
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
class PlaybackFixture:
    client: httpx.AsyncClient
    database: KatalogDatabase
    settings: KatalogSettings
    ids: dict[str, int]


@pytest.fixture
async def playback_fixture(tmp_path: Path) -> AsyncIterator[PlaybackFixture]:
    library_path = tmp_path / "library"
    library_path.mkdir()
    database_path = tmp_path / "katalog.sqlite3"
    database = KatalogDatabase(database_path)
    database.create_schema()
    with database.transaction() as session:
        root = create_library_root(session, path=library_path, expected_media_kind=ZaisanKind.MOVIE)
        movie = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="Kasana Movie",
        )
        unavailable = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="Unavailable Movie",
            availability=AvailabilityState.UNAVAILABLE,
        )
        series = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.SERIES,
            title="Kasana Series",
        )
        season = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.SEASON,
            parent_id=series.id,
            season_number=1,
            title="Season One",
        )
        episode_one = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.EPISODE,
            parent_id=season.id,
            season_number=1,
            episode_number=1,
            title="Episode One",
        )
        episode_two = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.EPISODE,
            parent_id=season.id,
            season_number=1,
            episode_number=2,
            title="Episode Two",
        )
        episode_three = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.EPISODE,
            parent_id=season.id,
            season_number=1,
            episode_number=3,
            title="Episode Three",
        )
        create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.SPECIAL,
            parent_id=series.id,
            title="Special",
        )
        paths = {
            "movie": library_path / "movie.mkv",
            "episode_one": library_path / "episode-one.mkv",
            "episode_two": library_path / "episode-two.mkv",
            "episode_three": library_path / "episode-three.mkv",
        }
        for name, path in paths.items():
            _write_sparse_media(path, prefix=b"Kasana" if name == "movie" else name.encode())
        for item, path in (
            (movie, paths["movie"]),
            (episode_one, paths["episode_one"]),
            (episode_two, paths["episode_two"]),
            (episode_three, paths["episode_three"]),
        ):
            stat = path.stat()
            attach_media_file(
                session,
                library_item_id=item.id,
                absolute_path=path,
                size_bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                container="matroska",
                duration_seconds=120.0,
                audio_streams=({"codec_name": "aac", "channels": 2},),
                subtitle_streams=({"codec_name": "subrip", "tags": {"language": "en"}},),
            )
        collection = create_collection(session, name="Mixed")
        for item in (movie, episode_one, episode_two):
            add_collection_membership(session, collection_id=collection.id, library_item_id=item.id)
        watch_order = create_watch_order(
            session,
            collection_id=collection.id,
            name="Mixed release order",
            order_kind=KeiroKind.CUSTOM,
        )
        for item in (movie, episode_one, episode_two):
            append_watch_order_entry(
                session, watch_order_id=watch_order.id, library_item_id=item.id
            )
        user = create_user(session, username="playback-user")
        record_playback_progress(
            session,
            user_id=user.id,
            library_item_id=episode_two.id,
            position_seconds=37.0,
            duration_seconds=120.0,
            completed=False,
        )
        ids = {
            "movie": movie.id,
            "unavailable": unavailable.id,
            "series": series.id,
            "episode_one": episode_one.id,
            "episode_two": episode_two.id,
            "episode_three": episode_three.id,
            "user": user.id,
            "watch_order": watch_order.id,
        }
    settings = KatalogSettings(
        database_path=database_path,
        artwork_cache_path=tmp_path / "artwork",
        user_configuration_directory=tmp_path / "users",
        playback_launch_token_ttl_seconds=30,
        media_access_token_ttl_seconds=30,
    )
    app = create_app(settings, database=database)
    runtime = KatalogApiRuntime(settings, database)
    app.state.runtime = runtime
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://katalog.test") as client:
        yield PlaybackFixture(client=client, database=database, settings=settings, ids=ids)
    await runtime.close()
    database.close()


async def test_standalone_launch_is_one_use_and_media_has_ranges(
    playback_fixture: PlaybackFixture, tmp_path: Path
) -> None:
    launch = await _create_plan(
        playback_fixture,
        {"kind": "standalone", "item_id": playback_fixture.ids["movie"]},
    )
    response = await playback_fixture.client.get(f"/api/v1/playback/plans/{launch}")

    assert response.status_code == 200
    session = response.json()
    entry = session["current_item"]
    assert entry["display_title"] == "Kasana Movie"
    assert entry["audio_streams"][0]["codec"] == "aac"
    assert entry["subtitle_streams"][0]["language"] == "en"
    assert str(tmp_path) not in json.dumps(session)
    reused = await playback_fixture.client.get(f"/api/v1/playback/plans/{launch}")
    assert reused.status_code == 404

    full = await playback_fixture.client.get(entry["stream_url"])
    assert full.status_code == 200
    assert full.headers["accept-ranges"] == "bytes"
    assert full.content[:6] == b"Kasana"
    partial = await playback_fixture.client.get(entry["stream_url"], headers={"Range": "bytes=1-3"})
    assert partial.status_code == 206
    assert partial.headers["content-range"].startswith("bytes 1-3/")
    assert partial.content == b"asa"
    head = await playback_fixture.client.head(entry["stream_url"], headers={"Range": "bytes=-2"})
    assert head.status_code == 206
    assert head.content == b""
    malformed = await playback_fixture.client.get(
        entry["stream_url"], headers={"Range": "bytes=nope"}
    )
    assert malformed.status_code == 416
    assert malformed.headers["content-range"].startswith("bytes */")
    download = await playback_fixture.client.get(entry["download_url"])
    assert download.headers["content-disposition"].startswith("attachment;")
    mismatched_operation = await playback_fixture.client.get(
        entry["download_url"].replace("downloads", "media")
    )
    assert mismatched_operation.status_code == 404


async def test_series_resume_watch_order_and_manual_queue_contexts(
    playback_fixture: PlaybackFixture,
) -> None:
    series_launch = await _create_plan(
        playback_fixture,
        {"kind": "series", "episode_id": playback_fixture.ids["episode_two"]},
    )
    series = (await playback_fixture.client.get(f"/api/v1/playback/plans/{series_launch}")).json()
    assert [entry["display_title"] for entry in series["entries"]] == [
        "Episode Two",
        "Episode Three",
    ]
    assert series["entries"][0]["series_title"] == "Kasana Series"
    assert all(entry["display_title"] != "Special" for entry in series["entries"])

    resume_launch = await _create_plan(
        playback_fixture,
        {"kind": "series", "series_id": playback_fixture.ids["series"], "resume": True},
    )
    resumed = (await playback_fixture.client.get(f"/api/v1/playback/plans/{resume_launch}")).json()
    assert resumed["current_item"]["item_id"] == playback_fixture.ids["episode_two"]
    assert resumed["current_item"]["saved_resume_position_seconds"] == 37.0

    order_launch = await _create_plan(
        playback_fixture,
        {
            "kind": "watch_order",
            "watch_order_id": playback_fixture.ids["watch_order"],
        },
    )
    ordered = (await playback_fixture.client.get(f"/api/v1/playback/plans/{order_launch}")).json()
    assert [entry["item_id"] for entry in ordered["entries"]] == [
        playback_fixture.ids["movie"],
        playback_fixture.ids["episode_one"],
        playback_fixture.ids["episode_two"],
    ]
    advanced = await playback_fixture.client.post(
        f"/api/v1/playback/sessions/{ordered['id']}/advance"
    )
    assert advanced.status_code == 200
    assert advanced.json()["context"]["watch_order_id"] == playback_fixture.ids["watch_order"]
    assert advanced.json()["current_item"]["item_id"] == playback_fixture.ids["episode_one"]

    manual_launch = await _create_plan(
        playback_fixture,
        {
            "kind": "manual_queue",
            "item_ids": [playback_fixture.ids["episode_three"], playback_fixture.ids["movie"]],
        },
    )
    manual = (await playback_fixture.client.get(f"/api/v1/playback/plans/{manual_launch}")).json()
    assert manual["context"]["kind"] == "manual_queue"
    assert [entry["item_id"] for entry in manual["entries"]] == [
        playback_fixture.ids["episode_three"],
        playback_fixture.ids["movie"],
    ]


async def test_progress_seek_completion_expiry_and_unavailable_items(
    playback_fixture: PlaybackFixture,
) -> None:
    unavailable = await playback_fixture.client.post(
        "/api/v1/playback/plans",
        json={
            "user_id": playback_fixture.ids["user"],
            "context": {"kind": "standalone", "item_id": playback_fixture.ids["unavailable"]},
        },
    )
    assert unavailable.status_code == 422

    launch = await _create_plan(
        playback_fixture,
        {"kind": "standalone", "item_id": playback_fixture.ids["movie"]},
    )
    session = (await playback_fixture.client.get(f"/api/v1/playback/plans/{launch}")).json()
    progress_path = f"/api/v1/playback/sessions/{session['id']}/progress"
    progressed = await playback_fixture.client.put(progress_path, json={"position_seconds": 20})
    assert progressed.status_code == 200
    non_monotonic = await playback_fixture.client.put(progress_path, json={"position_seconds": 10})
    assert non_monotonic.status_code == 422
    seek = await playback_fixture.client.put(
        progress_path, json={"position_seconds": 10, "seek": True}
    )
    assert seek.status_code == 200
    assert seek.json()["event"]["kind"] == "progress"
    completed = await playback_fixture.client.post(
        f"/api/v1/playback/sessions/{session['id']}/complete"
    )
    assert completed.status_code == 200
    assert completed.json()["event"]["kind"] == "completed"
    with playback_fixture.database.transaction() as database_session:
        state = database_session.scalar(
            select(PlaybackState).where(
                PlaybackState.user_id == playback_fixture.ids["user"],
                PlaybackState.library_item_id == playback_fixture.ids["movie"],
            )
        )
        assert state is not None and state.completed is True

    expired_launch = await _create_plan(
        playback_fixture,
        {"kind": "standalone", "item_id": playback_fixture.ids["movie"]},
    )
    with playback_fixture.database.transaction() as database_session:
        token = database_session.scalar(
            select(PlaybackLaunchToken).where(
                PlaybackLaunchToken.token_hash
                == hashlib.sha256(expired_launch.encode("ascii")).hexdigest()
            )
        )
        assert token is not None
        token.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert (
        await playback_fixture.client.get(f"/api/v1/playback/plans/{expired_launch}")
    ).status_code == 404

    with playback_fixture.database.transaction() as database_session:
        active = database_session.scalar(
            select(PlaybackSession).where(PlaybackSession.id == session["id"])
        )
        assert active is not None
        active.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    expired_session = await playback_fixture.client.get(
        f"/api/v1/playback/sessions/{session['id']}"
    )
    assert expired_session.status_code == 404


async def test_typed_client_and_stream_cancellation(playback_fixture: PlaybackFixture) -> None:
    app = create_app(playback_fixture.settings, database=playback_fixture.database)
    socket_handle = socket.socket()
    socket_handle.bind(("127.0.0.1", 0))
    socket_handle.listen()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=socket_handle.getsockname()[1],
            log_level="error",
            access_log=False,
        )
    )
    server_task = asyncio.create_task(server.serve(sockets=[socket_handle]))
    try:
        while not server.started:  # noqa: ASYNC110
            await asyncio.sleep(0.001)
        async with KatalogClient(f"http://127.0.0.1:{socket_handle.getsockname()[1]}") as client:
            launch = await client.create_playback_plan(
                PlaybackPlanRequest(
                    user_id=playback_fixture.ids["user"],
                    context=StandalonePlaybackContext(item_id=playback_fixture.ids["movie"]),
                )
            )
            playback_session = await client.launch_playback_plan(launch.launch_token)
            update = await client.update_playback_session_progress(
                playback_session.id, SessionProgressUpdate(position_seconds=1.0)
            )
            assert update.session.current_item is not None
            body = b"".join(
                [
                    chunk
                    async for chunk in client.stream_media(update.session.current_item.stream_url)
                ]
            )
            assert body.startswith(b"Kasana")
    finally:
        server.should_exit = True
        await server_task
        socket_handle.close()

    file_path = playback_fixture.settings.database_path.parent / "cancel.mkv"
    _write_sparse_media(file_path, prefix=b"cancel")
    media_file = MediaTransferFile(
        path=file_path,
        size_bytes=file_path.stat().st_size,
        content_type="video/x-matroska",
        etag='"cancel"',
        download_name="cancel.mkv",
        last_modified=datetime.now(UTC),
    )
    response = RangeStreamingFileTransferPolicy(chunk_size=1).response(
        media_file,
        method="GET",
        range_header=None,
        if_none_match=None,
        download=False,
    )
    assert isinstance(response, BoundedFileResponse)
    sent_chunk = asyncio.Event()
    never = asyncio.Event()

    async def receive() -> Message:
        await never.wait()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        if message["type"] == "http.response.body" and message.get("body"):
            sent_chunk.set()
            await never.wait()

    scope = cast(Scope, {"type": "http", "asgi": {"version": "3.0"}})
    pending = asyncio.create_task(response(scope, receive, send))
    await sent_chunk.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending


async def _create_plan(playback_fixture: PlaybackFixture, context: dict[str, object]) -> str:
    response = await playback_fixture.client.post(
        "/api/v1/playback/plans",
        json={"user_id": playback_fixture.ids["user"], "context": context},
    )
    assert response.status_code == 201, response.text
    return response.json()["launch_token"]


def _write_sparse_media(path: Path, *, prefix: bytes) -> None:
    with path.open("wb") as media_file:
        media_file.write(prefix)
        media_file.truncate(64 * 1024)

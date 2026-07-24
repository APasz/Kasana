"""Browser delivery classification and ephemeral FFmpeg lifecycle tests."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException
from nicegui.client import Client
from nicegui.page import page
from starlette.requests import Request

from kasana.kanvas import dashboard
from kasana.kanvas.ffmpeg import FFmpegError, FragmentedMp4Stream, start_fragmented_mp4
from kasana.kanvas.playback_compatibility import (
    BrowserMediaCapability,
    BrowserPlaybackCapabilities,
    PlaybackMode,
    classify_playback,
)
from kasana.kanvas.routes.browser_playback import render_browser_playback_card
from kasana.katalog.api.service import _stream_summary  # pyright: ignore[reportPrivateUsage]
from kasana.katalog.public import (
    PlaybackContext,
    PlaybackContextKind,
    PlaybackPlanEntry,
    PlaybackSessionResponse,
)


def _entry(
    *, container: str, video_codec: str = "h264", audio_codec: str = "aac"
) -> PlaybackPlanEntry:
    return PlaybackPlanEntry.model_validate(
        {
            "position": 0,
            "item_id": 1,
            "display_title": "Episode",
            "duration_seconds": 120,
            "saved_resume_position_seconds": 0,
            "stream_url": f"/api/v1/media/{'a' * 32}",
            "download_url": f"/api/v1/downloads/{'b' * 32}",
            "container": container,
            "video_streams": [{"codec": video_codec}],
            "audio_streams": [{"codec": audio_codec, "language": "en"}],
        }
    )


def test_h264_aac_mp4_direct_play_and_mkv_remux() -> None:
    capabilities = BrowserPlaybackCapabilities()

    assert classify_playback(
        _entry(container="isobmff"), capabilities, preferred_audio_language=None
    ).mode is PlaybackMode.DIRECT
    assert classify_playback(
        _entry(container="matroska"), capabilities, preferred_audio_language=None
    ).mode is PlaybackMode.REMUX


def test_scanner_codec_metadata_is_exposed_to_browser_playback() -> None:
    stream = _stream_summary({"codec": "h264", "tags": {"language": "eng"}})  # pyright: ignore[reportPrivateUsage]

    assert stream.codec == "h264"
    assert stream.language == "eng"


def test_incompatible_audio_uses_aac_conversion_without_video_transcoding() -> None:
    decision = classify_playback(
        _entry(container="matroska", audio_codec="ac3"),
        BrowserPlaybackCapabilities(),
        preferred_audio_language="en",
    )

    assert decision.mode is PlaybackMode.AUDIO_TRANSCODE
    assert decision.audio_stream_index == 0


def test_hevc_requires_positive_browser_evidence() -> None:
    entry = _entry(container="isobmff", video_codec="hevc")
    supported = BrowserPlaybackCapabilities(
        media=(
            BrowserMediaCapability(
                content_type='video/mp4; codecs="hvc1.1.6.L93.B0, mp4a.40.2"',
                media_capabilities_supported=True,
                can_play_type="probably",
            ),
        )
    )

    assert classify_playback(
        entry, BrowserPlaybackCapabilities(), preferred_audio_language=None
    ).mode is PlaybackMode.UNSUPPORTED
    assert (
        classify_playback(entry, supported, preferred_audio_language=None).mode
        is PlaybackMode.DIRECT
    )


class _ClosingProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    async def wait(self) -> int:
        return 0

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class _ReadPipe:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def read(self, _size: int = -1) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""


class _StreamingProcess(_ClosingProcess):
    def __init__(self, *, returncode: int | None = 0, stderr: bytes = b"") -> None:
        super().__init__()
        self.returncode = returncode
        self.stdout = _ReadPipe([b"fragment", b""])
        self.stderr = _ReadPipe([stderr])

    async def wait(self) -> int:
        return self.returncode if self.returncode is not None else 0


@pytest.mark.asyncio
async def test_ffmpeg_process_is_terminated_when_browser_stream_is_closed() -> None:
    process = _ClosingProcess()

    await FragmentedMp4Stream(process).close()  # type: ignore[arg-type]

    assert process.terminated is True
    assert process.killed is False


@pytest.mark.asyncio
async def test_fragmented_mp4_stream_reads_output_reports_failure_and_uses_copy_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    normal_process = _StreamingProcess()
    normal_stream = FragmentedMp4Stream(cast(asyncio.subprocess.Process, normal_process))
    streamed = [chunk async for chunk in normal_stream.chunks()]
    assert streamed == [b"fragment"]

    failed_process = _StreamingProcess(returncode=1, stderr=b"conversion failed")
    with pytest.raises(FFmpegError, match="conversion failed"):
        _ = [
            chunk
            async for chunk in FragmentedMp4Stream(
                cast(asyncio.subprocess.Process, failed_process)
            ).chunks()
        ]

    launched: list[str] = []

    async def create_process(*arguments: str, **_kwargs: object) -> object:
        launched.extend(arguments)
        return normal_process

    monkeypatch.setattr("kasana.kanvas.ffmpeg.asyncio.create_subprocess_exec", create_process)
    result = await start_fragmented_mp4(
        "ffmpeg",
        "http://katalog.test/api/v1/media/token",
        audio_stream_index=2,
        transcode_audio=True,
    )

    assert isinstance(result, FragmentedMp4Stream)
    assert ["-c:v", "copy"] == launched[launched.index("-c:v") : launched.index("-c:v") + 2]
    assert ["-c:a", "aac"] == launched[launched.index("-c:a") : launched.index("-c:a") + 2]
    assert "pipe:1" in launched


def test_playback_delivery_query_validation_keeps_direct_ranges_and_copy_boundary() -> None:
    request = Request(
        {"type": "http", "query_string": b"mode=remux&audioStream=0", "headers": []}
    )
    entry = _entry(container="matroska")

    mode, audio_index = dashboard._requested_playback_delivery(  # pyright: ignore[reportPrivateUsage]
        request
    )

    assert mode is PlaybackMode.REMUX
    assert audio_index == 0
    assert dashboard._valid_playback_delivery(  # pyright: ignore[reportPrivateUsage]
        entry, mode, audio_index
    )
    assert not dashboard._valid_playback_delivery(  # pyright: ignore[reportPrivateUsage]
        entry, PlaybackMode.DIRECT, audio_index
    )


@pytest.mark.asyncio
async def test_compatibility_endpoint_returns_remux_or_visible_kestrel_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = SimpleNamespace(id="s" * 32, current_item=_entry(container="matroska"))
    profile = SimpleNamespace(user=SimpleNamespace(id=1, preferred_audio_language=None))

    class FakePlaybackService:
        def __init__(self, *_args: object) -> None:
            pass

        async def playback_session(self, _session_id: str) -> object:
            return session

        async def create_kestrel_fallback_uri(self, _session: object) -> str:
            return f"kasana://play/{'z' * 32}"

    async def require_profile(_request: Request) -> object:
        return profile

    async def payload(_request: Request) -> dict[str, object]:
        return {"media": []}

    monkeypatch.setattr(dashboard, "KanvasPlaybackService", FakePlaybackService)
    monkeypatch.setattr(dashboard, "_require_profile", require_profile)
    monkeypatch.setattr(dashboard, "_json_object", payload)
    request = Request({"type": "http", "query_string": b"", "headers": []})

    remux_response = await dashboard.playback_compatibility("s" * 32, 0, request)
    remux_body = json.loads(bytes(remux_response.body))
    assert remux_body["mode"] == "remux"
    assert remux_body["mediaUrl"].endswith("mode=remux&audioStream=0")

    session.current_item = _entry(container="matroska", video_codec="av1")
    fallback_response = await dashboard.playback_compatibility("s" * 32, 0, request)
    fallback_body = json.loads(bytes(fallback_response.body))
    assert fallback_body == {
        "mode": "unsupported",
        "mediaUrl": None,
        "fallbackUri": f"kasana://play/{'z' * 32}",
    }


def test_next_episode_replaces_the_media_source_and_pagehide_flushes_progress() -> None:
    script = (Path(__file__).parents[1] / "src/kasana/kanvas/static/kanvas.js").read_text(
        encoding="utf-8"
    )

    assert "nextEntry" in script
    assert "await loadEntry(payload.nextEntry.position" in script
    assert "keepalive: true" in script
    assert "window.location.assign(payload.nextUrl)" not in script


def test_browser_playback_card_contains_a_source_less_compatibility_player() -> None:
    entry = _entry(container="isobmff")
    now = datetime.now(UTC)
    session = PlaybackSessionResponse(
        id="s" * 32,
        user_id=1,
        context=PlaybackContext(kind=PlaybackContextKind.STANDALONE, item_id=1),
        current_entry_position=0,
        current_item=entry,
        entries=(entry,),
        created_at=now,
        expires_at=now + timedelta(hours=1),
        closed_at=None,
    )

    with Client(page("")) as client:
        render_browser_playback_card(session)
        video_elements = [element for element in client.elements.values() if element.tag == "video"]
        fallback_links = [
            element
            for element in client.elements.values()
            if element.tag == "a" and "data-player-kestrel" in element._props  # pyright: ignore[reportPrivateUsage]
        ]

    assert len(video_elements) == 1
    assert "src" not in video_elements[0]._props  # pyright: ignore[reportPrivateUsage]
    assert len(fallback_links) == 1


def test_browser_playback_card_rejects_a_session_without_a_current_entry() -> None:
    now = datetime.now(UTC)
    session = PlaybackSessionResponse(
        id="s" * 32,
        user_id=1,
        context=PlaybackContext(kind=PlaybackContextKind.STANDALONE, item_id=1),
        current_entry_position=0,
        current_item=None,
        entries=(_entry(container="isobmff"),),
        created_at=now,
        expires_at=now + timedelta(hours=1),
        closed_at=None,
    )

    with pytest.raises(ValueError, match="current media item"):
        render_browser_playback_card(session)


def test_delivery_validation_rejects_invalid_query_and_unavailable_audio_stream() -> None:
    with pytest.raises(HTTPException):
        dashboard._requested_playback_delivery(  # pyright: ignore[reportPrivateUsage]
            Request({"type": "http", "query_string": b"mode=video-transcode", "headers": []})
        )
    assert not dashboard._valid_playback_delivery(  # pyright: ignore[reportPrivateUsage]
        _entry(container="matroska"), PlaybackMode.REMUX, 1
    )

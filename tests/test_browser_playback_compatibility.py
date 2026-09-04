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
from nicegui.element import Element
from nicegui.page import page
from starlette.requests import Request

from kasana.kanvas import dashboard
from kasana.kanvas.ffmpeg import (
    FFmpegError,
    FragmentedMp4Stream,
    start_font_attachment_extract,
    start_fragmented_mp4,
    start_subtitle_extract,
)
from kasana.kanvas.playback_compatibility import (
    BrowserMediaCapability,
    BrowserPlaybackCapabilities,
    PlaybackMode,
    classify_playback,
)
from kasana.kanvas.routes import api_playback
from kasana.kanvas.routes import pages as kanvas_pages
from kasana.kanvas.routes.browser_playback import render_browser_playback_card
from kasana.kanvas.subtitles import SubtitleConversionError, as_webvtt
from kasana.katalog.api.service import _stream_summary  # pyright: ignore[reportPrivateUsage]
from kasana.katalog.public import (
    KatalogClientError,
    KatalogClientErrorKind,
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


def _element_props(element: Element) -> dict[str, object]:
    """Expose NiceGUI's internal test-only rendered attributes."""

    return cast(dict[str, object], element._props)  # pyright: ignore[reportPrivateUsage]


def test_h264_aac_mp4_direct_play_and_mkv_remux() -> None:
    capabilities = BrowserPlaybackCapabilities()

    assert (
        classify_playback(
            _entry(container="isobmff"), capabilities, preferred_audio_language=None
        ).mode
        is PlaybackMode.DIRECT
    )
    assert (
        classify_playback(
            _entry(container="matroska"), capabilities, preferred_audio_language=None
        ).mode
        is PlaybackMode.REMUX
    )


def test_scanner_codec_metadata_is_exposed_to_browser_playback() -> None:
    stream = _stream_summary({"codec": "h264", "tags": {"language": "eng"}})  # pyright: ignore[reportPrivateUsage]

    assert stream.codec == "h264"
    assert stream.language == "eng"


def test_srt_is_converted_to_webvtt_and_generated_stream_offsets_shift_cues() -> None:
    converted = as_webvtt(
        b"1\n00:00:05,000 --> 00:00:07,000\nHello\n\n2\n00:00:10,000 --> 00:00:12,000\nWorld\n",
        source_is_webvtt=False,
        offset_seconds=6.0,
    ).decode()

    assert converted.startswith("WEBVTT\n")
    assert "00:00:00.000 --> 00:00:01.000\nHello" in converted
    assert "00:00:04.000 --> 00:00:06.000\nWorld" in converted


def test_webvtt_timing_adjustment_preserves_cue_placement() -> None:
    converted = as_webvtt(
        b"WEBVTT\n\n00:00:05.000 --> 00:00:07.000 line:10% position:25%\nHello\n",
        source_is_webvtt=True,
        offset_seconds=6.0,
        timing_offset_seconds=0.5,
    ).decode()

    assert "00:00:00.000 --> 00:00:01.500 line:10% position:25%\nHello" in converted


def test_webvtt_conversion_rejects_malformed_or_non_utf8_text() -> None:
    assert as_webvtt(
        b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello\n",
        source_is_webvtt=True,
        offset_seconds=0,
    ).startswith(b"WEBVTT\n")
    with pytest.raises(SubtitleConversionError):
        as_webvtt(b"not VTT", source_is_webvtt=True, offset_seconds=0)
    with pytest.raises(SubtitleConversionError):
        as_webvtt(b"\xff", source_is_webvtt=False, offset_seconds=0)
    with pytest.raises(SubtitleConversionError):
        as_webvtt(b"1\nNo timing\n", source_is_webvtt=False, offset_seconds=0)
    with pytest.raises(SubtitleConversionError):
        as_webvtt(b"WEBVTT\n", source_is_webvtt=True, offset_seconds=-1)


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

    assert (
        classify_playback(entry, BrowserPlaybackCapabilities(), preferred_audio_language=None).mode
        is PlaybackMode.UNSUPPORTED
    )
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
        start_seconds=42.5,
    )

    assert isinstance(result, FragmentedMp4Stream)
    assert ["-c:v", "copy"] == launched[launched.index("-c:v") : launched.index("-c:v") + 2]
    assert ["-c:a", "aac"] == launched[launched.index("-c:a") : launched.index("-c:a") + 2]
    assert [
        "-noaccurate_seek",
        "-ss",
        "42.500",
        "-i",
        "http://katalog.test/api/v1/media/token",
    ] == launched[launched.index("-noaccurate_seek") : launched.index("-i") + 2]
    assert "pipe:1" in launched

    remux_command_start = len(launched)
    remux = await start_fragmented_mp4(
        "ffmpeg",
        "http://katalog.test/api/v1/media/token",
        audio_stream_index=2,
        transcode_audio=False,
        start_seconds=42.5,
    )
    assert isinstance(remux, FragmentedMp4Stream)
    remux_command = launched[remux_command_start:]
    assert "-noaccurate_seek" not in remux_command

    subtitle_command_start = len(launched)
    subtitle = await start_subtitle_extract(
        "ffmpeg",
        "http://katalog.test/api/v1/media/token",
        subtitle_stream_index=1,
        ass=True,
    )
    assert isinstance(subtitle, FragmentedMp4Stream)
    subtitle_command = launched[subtitle_command_start:]
    assert ["-map", "0:s:1"] == subtitle_command[
        subtitle_command.index("-map") : subtitle_command.index("-map") + 2
    ]
    assert ["-c:s", "ass", "-f", "ass"] == subtitle_command[
        subtitle_command.index("-c:s") : subtitle_command.index("-c:s") + 4
    ]
    with pytest.raises(ValueError, match="cannot be negative"):
        await start_subtitle_extract(
            "ffmpeg", "http://katalog.test/media", subtitle_stream_index=-1, ass=False
        )

    font_command_start = len(launched)
    font = await start_font_attachment_extract(
        "ffmpeg", "http://katalog.test/api/v1/media/token", stream_index=4
    )
    assert isinstance(font, FragmentedMp4Stream)
    font_command = launched[font_command_start:]
    assert ["-dump_attachment:4", "pipe:1"] == font_command[
        font_command.index("-dump_attachment:4") : font_command.index("-dump_attachment:4") + 2
    ]
    assert ["-frames:v", "0"] == font_command[
        font_command.index("-frames:v") : font_command.index("-frames:v") + 2
    ]
    with pytest.raises(ValueError, match="cannot be negative"):
        await start_font_attachment_extract("ffmpeg", "http://katalog.test/media", stream_index=-1)


def test_playback_delivery_query_validation_keeps_direct_ranges_and_copy_boundary() -> None:
    request = Request({"type": "http", "query_string": b"mode=remux&audioStream=0", "headers": []})
    entry = _entry(container="matroska")

    mode, audio_index = api_playback._requested_playback_delivery(  # pyright: ignore[reportPrivateUsage]
        request
    )

    assert mode is PlaybackMode.REMUX
    assert audio_index == 0
    assert api_playback._valid_playback_delivery(  # pyright: ignore[reportPrivateUsage]
        entry, mode, audio_index
    )
    assert not api_playback._valid_playback_delivery(  # pyright: ignore[reportPrivateUsage]
        entry, PlaybackMode.DIRECT, audio_index
    )

    seek_request = Request({"type": "http", "query_string": b"startSeconds=30.5", "headers": []})
    assert (
        api_playback._requested_stream_start_seconds(  # pyright: ignore[reportPrivateUsage]
            seek_request, entry.duration_seconds
        )
        == 30.5
    )

    with pytest.raises(HTTPException):
        api_playback._requested_stream_start_seconds(  # pyright: ignore[reportPrivateUsage]
            Request({"type": "http", "query_string": b"startSeconds=nan", "headers": []}),
            entry.duration_seconds,
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

    monkeypatch.setattr(api_playback, "KanvasPlaybackService", FakePlaybackService)
    monkeypatch.setattr(api_playback, "require_profile", require_profile)
    monkeypatch.setattr(api_playback, "json_object", payload)
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


def test_subtitle_request_helpers_reject_invalid_offsets_and_track_ids() -> None:
    entry = _entry(container="isobmff")
    assert (
        api_playback._requested_subtitle_offset_seconds(  # pyright: ignore[reportPrivateUsage]
            Request({"type": "http", "query_string": b"offsetSeconds=42.5", "headers": []}),
            entry.duration_seconds,
        )
        == 42.5
    )
    assert api_playback._optional_track_id("embedded-3") == "embedded-3"  # pyright: ignore[reportPrivateUsage]
    assert api_playback._optional_track_id(None) is None  # pyright: ignore[reportPrivateUsage]
    assert api_playback._is_webvtt_track("webvtt")  # pyright: ignore[reportPrivateUsage]
    assert (
        api_playback._requested_subtitle_timing_offset_seconds(  # pyright: ignore[reportPrivateUsage]
            Request(
                {
                    "type": "http",
                    "query_string": b"timingOffsetMilliseconds=-500",
                    "headers": [],
                }
            ),
            default_milliseconds=0,
        )
        == -0.5
    )
    assert (
        api_playback._requested_subtitle_timing_offset_seconds(  # pyright: ignore[reportPrivateUsage]
            Request({"type": "http", "query_string": b"", "headers": []}),
            default_milliseconds=500,
        )
        == 0.5
    )
    with pytest.raises(HTTPException):
        api_playback._requested_subtitle_offset_seconds(  # pyright: ignore[reportPrivateUsage]
            Request({"type": "http", "query_string": b"offsetSeconds=nan", "headers": []}),
            entry.duration_seconds,
        )
    with pytest.raises(ValueError):
        api_playback._optional_track_id("not-a-track")  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(HTTPException):
        api_playback._requested_subtitle_timing_offset_seconds(  # pyright: ignore[reportPrivateUsage]
            Request(
                {
                    "type": "http",
                    "query_string": b"timingOffsetMilliseconds=30001",
                    "headers": [],
                }
            ),
            default_milliseconds=0,
        )


def test_next_episode_preserves_fullscreen_then_transitions_its_item_page() -> None:
    script = (Path(__file__).parents[1] / "src/kasana/kanvas/static/kanvas.js").read_text(
        encoding="utf-8"
    )

    assert "nextUrl" in script
    assert "payload.nextEntry" in script
    assert "await loadEntry(" in script
    assert "pendingItemPageUrl" in script
    assert "navigateToPendingItemPage" in script
    assert "window.location.assign(itemPageAutoplayUrl(nextUrl))" in script
    assert "subtitlesDisabledByProfile" in script
    assert "reconnectPlaybackStream" in script
    assert "Playback stream stopped. Reload this page to retry." in script
    assert "video.loop = false;" in script
    assert "body: JSON.stringify({entryPosition})" in script
    assert "entryPosition})," in script
    assert "keepalive: true" in script


def test_browser_player_bundles_libass_and_keeps_track_switches_at_absolute_time() -> None:
    repository_root = Path(__file__).parents[1]
    script = (repository_root / "src/kasana/kanvas/static/kanvas.js").read_text(encoding="utf-8")
    player = (repository_root / "src/kasana/kanvas/routes/browser_playback.py").read_text(
        encoding="utf-8"
    )
    head = (repository_root / "src/kasana/kanvas/components/shell.py").read_text(encoding="utf-8")
    libass = repository_root / "src/kasana/kanvas/static/libass/subtitles-octopus.js"

    assert "_PlayerControlAction.AUDIO" in player
    assert "_PlayerControlAction.SUBTITLES" in player
    assert player.index("IconName.SUBTITLES") < player.index("IconName.VOLUME")
    assert "data-player-subtitle-unsupported" in player
    assert "data-player-subtitle-timing-step" in player
    assert "data-player-subtitle-font-scale-step" in player
    assert "data-player-subtitle-position" in player
    assert "data-player-subtitle-appearance" in player
    assert "data-player-ass-font" in player
    assert "SubtitlesOctopus" in script
    assert "fonts: assFontUrls()" in script
    assert "timeOffset: streamStartSeconds - subtitleTimingOffsetMilliseconds / 1000" in script
    assert "subtitleOffsetMilliseconds" in script
    assert "subtitleFontScalePercent" in script
    assert "subtitleVerticalPosition" in script
    assert "timingOffsetMilliseconds" in script
    assert "CSS.supports('selector(video::cue)')" in script
    assert "subtitleAppearance.hidden = !appearanceAvailable" in script
    assert "applyNativeSubtitlePosition" in script
    assert "applyNativeSubtitleTiming" in script
    assert "queueTrackSelectionSave" in script
    assert "pendingDirectSeek" in script
    assert "persistTrackSelection" in script
    assert "await selectDelivery(autoplay, position)" in script
    assert (
        "const shouldPlayOnLoad = playOnLoad || (resumePosition > 0 && autoplayOnResume);" in script
    )
    assert "Open in Kestrel for this subtitle" in script
    assert "libass_script" in head
    assert libass.is_file()
    assert "cdn" not in head.casefold()


def test_webvtt_appearance_explicitly_overrides_native_caption_defaults() -> None:
    stylesheet = (Path(__file__).parents[1] / "src/kasana/kanvas/static/kanvas.css").read_text(
        encoding="utf-8"
    )

    assert "background-color: transparent !important" in stylesheet
    assert "text-shadow: none !important" in stylesheet
    assert 'data-subtitle-font-scale="75"' in stylesheet
    assert 'data-subtitle-font-scale="200"' in stylesheet


@pytest.mark.asyncio
async def test_browser_completion_returns_the_next_item_playback_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    next_entry = _entry(container="isobmff").model_copy(
        update={
            "position": 1,
            "display_title": "Pilot",
            "series_title": "Example Show",
            "context_label": "S01 E02",
        }
    )
    profile = SimpleNamespace(user=SimpleNamespace(id=1))
    calls: list[tuple[str, int]] = []

    class FakePlaybackService:
        def __init__(self, *_args: object) -> None:
            pass

        async def complete_playback_entry(
            self, session_id: str, expected_entry_position: int
        ) -> object:
            calls.append((session_id, expected_entry_position))
            return SimpleNamespace(current_entry_position=1, current_item=next_entry)

    class JsonRequest:
        async def json(self) -> dict[str, int]:
            return {"entryPosition": 0}

    async def require_profile(_request: Request) -> object:
        return profile

    monkeypatch.setattr(api_playback, "KanvasPlaybackService", FakePlaybackService)
    monkeypatch.setattr(api_playback, "require_profile", require_profile)

    response = await dashboard.complete_playback("s" * 32, cast(Request, JsonRequest()))

    assert calls == [("s" * 32, 0)]
    payload = json.loads(bytes(response.body))
    assert payload["nextUrl"] == f"/item/{next_entry.item_id}?playbackSession={'s' * 32}"
    assert payload["nextEntry"] == {
        "position": 1,
        "itemId": next_entry.item_id,
        "displayTitle": next_entry.display_title,
        "fullscreenTitle": "Example Show · Pilot",
        "specialInfo": "S01 E02",
        "durationSeconds": next_entry.duration_seconds,
        "savedResumePositionSeconds": next_entry.saved_resume_position_seconds,
        "audioStreams": [
            {"codec": "aac", "language": "en", "title": None},
        ],
        "subtitleTracks": [],
        "subtitleFontIds": [],
        "selectedAudioStream": 0,
        "selectedSubtitleTrack": None,
        "subtitleTimingOffsetMilliseconds": 0,
        "subtitleFontScalePercent": 100,
        "subtitleBackground": False,
        "subtitleShadow": False,
        "subtitleVerticalPosition": "author",
    }
    assert "stream_url" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_browser_current_completion_preserves_the_queue_position(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = SimpleNamespace(user=SimpleNamespace(id=1))
    calls: list[tuple[str, int]] = []

    class FakePlaybackService:
        def __init__(self, *_args: object) -> None:
            pass

        async def complete_current_playback_entry(
            self, session_id: str, expected_entry_position: int
        ) -> None:
            calls.append((session_id, expected_entry_position))

    class JsonRequest:
        async def json(self) -> dict[str, int]:
            return {"entryPosition": 0}

    async def require_profile(_request: Request) -> object:
        return profile

    monkeypatch.setattr(api_playback, "KanvasPlaybackService", FakePlaybackService)
    monkeypatch.setattr(api_playback, "require_profile", require_profile)

    response = await dashboard.complete_current_playback("s" * 32, cast(Request, JsonRequest()))

    assert response.status_code == 204
    assert calls == [("s" * 32, 0)]


@pytest.mark.asyncio
async def test_explicit_start_is_retained_when_playback_redirects_to_its_current_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = SimpleNamespace(id="s" * 32, current_item=SimpleNamespace(item_id=2))
    profile = SimpleNamespace(user=SimpleNamespace(id=1))

    class FakePlaybackService:
        def __init__(self, *_args: object) -> None:
            pass

        async def playback_session(self, _session_id: str) -> object:
            return session

    async def page_profile(_request: Request) -> object:
        return profile

    monkeypatch.setattr(kanvas_pages, "KanvasPlaybackService", FakePlaybackService)
    monkeypatch.setattr(kanvas_pages, "page_profile", page_profile)

    response = await dashboard.item_page(
        1,
        Request(
            {
                "type": "http",
                "query_string": b"playbackSession=" + b"s" * 32 + b"&start=true",
                "headers": [],
            }
        ),
    )

    assert response is not None
    assert response.headers["location"] == f"/item/2?playbackSession={'s' * 32}&start=true"


@pytest.mark.asyncio
async def test_expired_playback_session_returns_to_the_requested_item_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = SimpleNamespace(user=SimpleNamespace(id=1))

    class FakePlaybackService:
        def __init__(self, *_args: object) -> None:
            pass

        async def playback_session(self, _session_id: str) -> object:
            raise KatalogClientError(
                KatalogClientErrorKind.NOT_FOUND, "Playback session is unavailable."
            )

    async def page_profile(_request: Request) -> object:
        return profile

    monkeypatch.setattr(kanvas_pages, "KanvasPlaybackService", FakePlaybackService)
    monkeypatch.setattr(kanvas_pages, "page_profile", page_profile)

    response = await dashboard.item_page(
        2,
        Request(
            {
                "type": "http",
                "query_string": b"playbackSession=" + b"s" * 32 + b"&start=true",
                "headers": [],
            }
        ),
    )

    assert response is not None
    assert response.headers["location"] == "/item/2"


@pytest.mark.asyncio
async def test_play_route_starts_new_on_deck_items_but_respects_true_resume_autoplay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = SimpleNamespace(
        id="s" * 32,
        current_item=SimpleNamespace(item_id=2, saved_resume_position_seconds=0),
    )
    profile = SimpleNamespace(user=SimpleNamespace(id=1))

    class FakePlaybackService:
        def __init__(self, *_args: object) -> None:
            pass

        async def create_item_playback_session(self, _item_id: int, *, resume: bool) -> object:
            return session

    async def page_profile(_request: Request) -> object:
        return profile

    monkeypatch.setattr(kanvas_pages, "KanvasPlaybackService", FakePlaybackService)
    monkeypatch.setattr(kanvas_pages, "page_profile", page_profile)

    played = await dashboard.play_item_page(
        2, Request({"type": "http", "query_string": b"resume=false", "headers": []})
    )
    resumed = await dashboard.play_item_page(
        2, Request({"type": "http", "query_string": b"resume=true", "headers": []})
    )
    on_deck_new = await dashboard.play_item_page(
        2,
        Request(
            {
                "type": "http",
                "query_string": b"resume=true&onDeck=true",
                "headers": [],
            }
        ),
    )
    session.current_item.saved_resume_position_seconds = 37
    on_deck_resume = await dashboard.play_item_page(
        2,
        Request(
            {
                "type": "http",
                "query_string": b"resume=true&onDeck=true",
                "headers": [],
            }
        ),
    )

    assert played is not None
    assert played.headers["location"] == f"/item/2?playbackSession={'s' * 32}&start=true"
    assert resumed is not None
    assert resumed.headers["location"] == f"/item/2?playbackSession={'s' * 32}"
    assert on_deck_new is not None
    assert on_deck_new.headers["location"] == f"/item/2?playbackSession={'s' * 32}&start=true"
    assert on_deck_resume is not None
    assert on_deck_resume.headers["location"] == f"/item/2?playbackSession={'s' * 32}"


def test_browser_player_and_watch_order_controls_explain_explicit_unavailable_skips() -> None:
    repository_root = Path(__file__).parents[1]
    player = (repository_root / "src/kasana/kanvas/routes/browser_playback.py").read_text(
        encoding="utf-8"
    )
    collection_route = (repository_root / "src/kasana/kanvas/routes/collections.py").read_text(
        encoding="utf-8"
    )
    script = (repository_root / "src/kasana/kanvas/static/kanvas.js").read_text(encoding="utf-8")

    assert "Skipped unavailable entries" in player
    assert "Play available entries" in collection_route
    assert "Use Play available entries to skip it" in script


def test_browser_playback_card_contains_a_source_less_compatibility_player() -> None:
    entry = _entry(container="isobmff").model_copy(
        update={
            "display_title": "Pilot",
            "series_title": "Example Show",
            "context_label": "S01 E02",
        }
    )
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
        player_elements = [
            element
            for element in client.elements.values()
            if element.tag == "kanvas-playback-player"
        ]
        fallback_links = [
            element
            for element in client.elements.values()
            if element.tag == "a" and "data-player-kestrel" in element._props  # pyright: ignore[reportPrivateUsage]
        ]
        queues = [
            element
            for element in client.elements.values()
            if "k-playback-queue" in element._classes  # pyright: ignore[reportPrivateUsage]
        ]
        toggle_controls = [
            element
            for element in client.elements.values()
            if _element_props(element).get("data-player-action") == "toggle"
        ]
        frame_toggle_controls = [
            element
            for element in toggle_controls
            if "data-player-frame-toggle" in _element_props(element)
        ]
        transport_toggle_controls = [
            element
            for element in toggle_controls
            if "data-player-frame-toggle" not in _element_props(element)
        ]
        overflow_controls = [
            element
            for element in client.elements.values()
            if _element_props(element).get("data-player-action") == "overflow"
        ]
        theatre_controls = [
            element
            for element in client.elements.values()
            if _element_props(element).get("data-player-action") == "theatre"
        ]
        timeline_previews = [
            element
            for element in client.elements.values()
            if "data-player-timeline-preview" in _element_props(element)
        ]
        mobile_menus = [
            element
            for element in client.elements.values()
            if "data-player-mobile-menu" in _element_props(element)
        ]
        popup_controls = [
            element
            for element in client.elements.values()
            if _element_props(element).get("data-player-action")
            in {"menu", "audio", "subtitles", "overflow"}
        ]
        popup_panels = [
            element
            for element in client.elements.values()
            if any(
                attribute in _element_props(element)
                for attribute in (
                    "data-player-context-menu",
                    "data-player-audio-menu",
                    "data-player-subtitle-menu",
                    "data-player-mobile-menu",
                )
            )
        ]
        tooltips = [
            element
            for element in client.elements.values()
            if "data-player-tooltip-host" in _element_props(element)
        ]
        native_controls = [
            element
            for element in client.elements.values()
            if "data-player-native-controls" in _element_props(element)
        ]
        volume_controls = [
            element
            for element in client.elements.values()
            if any(
                attribute in _element_props(element)
                for attribute in ("data-player-volume", "data-player-mobile-volume")
            )
        ]
        volume_value_labels = [
            element
            for element in client.elements.values()
            if "data-player-volume-value" in _element_props(element)
        ]
        context_toggles = [
            element
            for element in client.elements.values()
            if "k-player__context-toggle" in element._classes  # pyright: ignore[reportPrivateUsage]
        ]
        autoplay_next_controls = [
            element
            for element in client.elements.values()
            if "data-player-autoplay-next" in _element_props(element)
        ]
        fullscreen_title = next(
            element
            for element in client.elements.values()
            if "k-player__fullscreen-title" in element._classes  # pyright: ignore[reportPrivateUsage]
        )
        fullscreen_special_info = next(
            element
            for element in client.elements.values()
            if "k-player__fullscreen-special-info" in element._classes  # pyright: ignore[reportPrivateUsage]
        )
        fullscreen_time = next(
            element
            for element in client.elements.values()
            if "k-player__fullscreen-time" in element._classes  # pyright: ignore[reportPrivateUsage]
        )
        frame_alignment_controls = [
            element
            for element in client.elements.values()
            if "data-player-frame-alignment-controls" in _element_props(element)
        ]
        frame_alignment_options = [
            element
            for element in client.elements.values()
            if "data-player-frame-alignment-option" in _element_props(element)
        ]

    assert len(video_elements) == 1
    assert "src" not in video_elements[0]._props  # pyright: ignore[reportPrivateUsage]
    assert "autoplay" not in video_elements[0]._props  # pyright: ignore[reportPrivateUsage]
    assert len(player_elements) == 1
    assert player_elements[0]._props["duration-seconds"] == "120"  # pyright: ignore[reportPrivateUsage]
    assert player_elements[0]._props["autoplay-on-resume"] == "false"  # pyright: ignore[reportPrivateUsage]
    assert player_elements[0]._props["play-on-load"] == "false"  # pyright: ignore[reportPrivateUsage]
    assert len(fallback_links) == 1
    assert queues == []
    assert len(toggle_controls) == 2
    assert len(transport_toggle_controls) == 1
    assert "k-player__control--toggle" in transport_toggle_controls[0]._classes  # pyright: ignore[reportPrivateUsage]
    assert len(frame_toggle_controls) == 1
    assert "k-player__frame-toggle" in frame_toggle_controls[0]._classes  # pyright: ignore[reportPrivateUsage]
    assert len(overflow_controls) == 1
    assert len(theatre_controls) == 2
    assert all(
        _element_props(theatre_control)["aria-pressed"] == "false"
        for theatre_control in theatre_controls
    )
    assert len(timeline_previews) == 1
    assert len(mobile_menus) == 1
    assert len(popup_controls) == 7
    assert all(_element_props(control)["aria-expanded"] == "false" for control in popup_controls)
    assert len(popup_panels) == 4
    assert all(_element_props(panel)["role"] == "group" for panel in popup_panels)
    assert {_element_props(panel)["aria-label"] for panel in popup_panels} == {
        "Audio tracks",
        "More playback controls",
        "Playback settings",
        "Subtitle tracks",
    }
    assert all("hidden" in _element_props(panel) for panel in popup_panels)
    assert toggle_controls[0]._props["data-player-tooltip"] == "Play"  # pyright: ignore[reportPrivateUsage]
    assert len(tooltips) == 1
    assert "hidden" in tooltips[0]._props  # pyright: ignore[reportPrivateUsage]
    assert len(native_controls) == 1
    assert native_controls == context_toggles
    assert native_controls[0].tag == "input"
    assert _element_props(native_controls[0])["type"] == "checkbox"
    assert _element_props(native_controls[0])["role"] == "switch"
    assert "checked" not in _element_props(native_controls[0])
    assert len(volume_controls) == 2
    assert all(_element_props(control)["aria-valuetext"] == "100%" for control in volume_controls)
    assert len(volume_value_labels) == 2
    assert all(
        "k-player__volume-value" in label._classes  # pyright: ignore[reportPrivateUsage]
        for label in volume_value_labels
    )
    assert autoplay_next_controls == []
    assert fullscreen_title._text == "Example Show · Pilot"  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]
    assert fullscreen_special_info._text == "S01 E02"  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]
    assert fullscreen_time._props["aria-label"] == "Current time"  # pyright: ignore[reportPrivateUsage]
    assert "data-player-fullscreen-time" in fullscreen_time._props  # pyright: ignore[reportPrivateUsage]
    assert len(frame_alignment_controls) == 1
    assert "hidden" in _element_props(frame_alignment_controls[0])
    assert _element_props(frame_alignment_controls[0])["aria-label"] == "Video alignment"
    alignment_values = [
        _element_props(option)["data-player-frame-alignment-option"]
        for option in frame_alignment_options
    ]
    assert alignment_values == [
        "start",
        "centred",
        "end",
    ]
    assert [_element_props(option)["aria-pressed"] for option in frame_alignment_options] == [
        "false",
        "true",
        "false",
    ]

    with Client(page("")) as client:
        render_browser_playback_card(session, play_on_load=True)
        started_player = next(
            element
            for element in client.elements.values()
            if element.tag == "kanvas-playback-player"
        )

    assert started_player._props["play-on-load"] == "true"  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_stale_progress_after_stopping_a_session_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = SimpleNamespace(user=SimpleNamespace(id=1))

    class FakePlaybackService:
        def __init__(self, *_args: object) -> None:
            pass

        async def report_playback_progress(self, _session_id: str, _update: object) -> None:
            raise KatalogClientError(
                KatalogClientErrorKind.NOT_FOUND, "Playback session is unavailable."
            )

    async def require_profile(_request: Request) -> object:
        return profile

    async def payload(_request: Request) -> dict[str, object]:
        return {"positionSeconds": 30, "entryPosition": 0}

    monkeypatch.setattr(api_playback, "KanvasPlaybackService", FakePlaybackService)
    monkeypatch.setattr(api_playback, "require_profile", require_profile)
    monkeypatch.setattr(api_playback, "json_object", payload)

    response = await dashboard.playback_progress("s" * 32, Request({"type": "http", "headers": []}))

    assert response.status_code == 204


def test_browser_playback_card_renders_a_disclosed_remaining_queue() -> None:
    now = datetime.now(UTC)
    entries = tuple(
        PlaybackPlanEntry.model_validate(
            {
                **_entry(container="isobmff").model_dump(mode="json"),
                "position": position,
                "item_id": position + 1,
                "display_title": title,
                "series_title": "Example series",
                "season_number": 2,
                "episode_number": position + 1,
            }
        )
        for position, title in enumerate(("Earlier episode", "Current episode", "Next episode"))
    )
    session = PlaybackSessionResponse(
        id="s" * 32,
        user_id=1,
        context=PlaybackContext(kind=PlaybackContextKind.STANDALONE, item_id=2),
        current_entry_position=1,
        current_item=entries[1],
        entries=entries,
        created_at=now,
        expires_at=now + timedelta(hours=1),
        closed_at=None,
    )

    with Client(page("")) as client:
        render_browser_playback_card(session)
        queue = next(
            element
            for element in client.elements.values()
            if "k-playback-queue" in element._classes  # pyright: ignore[reportPrivateUsage]
        )
        queue_entries = [
            element
            for element in client.elements.values()
            if "k-playback-queue__entry" in element._classes  # pyright: ignore[reportPrivateUsage]
        ]
        queue_actions = [
            element
            for element in client.elements.values()
            if "k-playback-queue__advance" in element._classes  # pyright: ignore[reportPrivateUsage]
        ]
        autoplay_next_controls = [
            element
            for element in client.elements.values()
            if "data-player-autoplay-next" in _element_props(element)
        ]
        queue_titles: list[str] = [
            cast(str, element._text)  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]
            for element in client.elements.values()
            if "k-playback-queue__title" in element._classes  # pyright: ignore[reportPrivateUsage]
        ]

    assert queue.tag == "details"
    assert "open" not in queue._props  # pyright: ignore[reportPrivateUsage]
    assert len(queue_entries) == 1
    assert len(queue_actions) == 1
    assert "data-player-next" in queue_actions[0]._props  # pyright: ignore[reportPrivateUsage]
    fullscreen_next_controls = [
        element
        for element in client.elements.values()
        if _element_props(element).get("data-player-action") == "next"
    ]
    assert len(fullscreen_next_controls) == 1
    assert "k-player__control--next" in fullscreen_next_controls[0]._classes  # pyright: ignore[reportPrivateUsage]
    assert len(autoplay_next_controls) == 1
    assert "checked" in _element_props(autoplay_next_controls[0])
    assert "k-player__context-toggle" in autoplay_next_controls[0]._classes  # pyright: ignore[reportPrivateUsage]
    assert _element_props(autoplay_next_controls[0])["role"] == "switch"
    assert queue_titles == ["Next episode", "Next episode"]


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
        api_playback._requested_playback_delivery(  # pyright: ignore[reportPrivateUsage]
            Request({"type": "http", "query_string": b"mode=video-transcode", "headers": []})
        )
    assert not api_playback._valid_playback_delivery(  # pyright: ignore[reportPrivateUsage]
        _entry(container="matroska"), PlaybackMode.REMUX, 1
    )

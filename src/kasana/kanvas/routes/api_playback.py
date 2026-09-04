"""Download and browser-playback HTTP endpoints for Kanvas."""

from __future__ import annotations

import logging
from asyncio import CancelledError
from collections.abc import AsyncIterable, AsyncIterator, Mapping
from math import isfinite
from typing import Literal, Protocol, cast
from urllib.parse import urljoin

from fastapi import HTTPException
from nicegui import app
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from starlette.types import Receive, Scope, Send

from kasana.kanvas.downloads import valid_download_csrf_token
from kasana.kanvas.ffmpeg import (
    FFmpegError,
    start_font_attachment_extract,
    start_fragmented_mp4,
    start_subtitle_extract,
)
from kasana.kanvas.katalog_clients import (
    create_katalog_client,
)
from kasana.kanvas.playback_compatibility import (
    BrowserPlaybackCapabilities,
    PlaybackMode,
    classify_playback,
)
from kasana.kanvas.services.katalog import KanvasKatalogService
from kasana.kanvas.services.playback import KanvasPlaybackService
from kasana.kanvas.subtitles import SubtitleConversionError, as_webvtt
from kasana.kanvas.viewmodels.playback import (
    BrowserPlaybackCompletionView,
    BrowserPlaybackEntryView,
)
from kasana.katalog.public import (
    MAX_SUBTITLE_TIMING_OFFSET_MILLISECONDS,
    KatalogClient,
    KatalogClientError,
    KatalogClientErrorKind,
    PlaybackPlanEntry,
    PlaybackSessionTrackSelection,
    PlaybackSubtitleFontAttachment,
    PlaybackSubtitleFontFormat,
    PlaybackSubtitleFormat,
    PlaybackSubtitleSource,
    SessionProgressUpdate,
)

from .common import (
    boolean,
    form_integer,
    form_value,
    integer,
    invalid_action,
    json_object,
    katalog_data_error,
    nonnegative_integer,
    require_profile,
    signed_integer,
)
from .runtime import runtime

_LOGGER = logging.getLogger(__name__)
_MAX_BROWSER_SUBTITLE_BYTES = 8 * 1024 * 1024
_MAX_BROWSER_SUBTITLE_FONT_BYTES = 16 * 1024 * 1024
_SUBTITLE_CACHE_CONTROL = "private, max-age=300"
_SUBTITLE_FONT_CONTENT_TYPES: Mapping[PlaybackSubtitleFontFormat, str] = {
    PlaybackSubtitleFontFormat.TRUETYPE: "font/ttf",
    PlaybackSubtitleFontFormat.OPENTYPE: "font/otf",
    PlaybackSubtitleFontFormat.COLLECTION: "font/collection",
}


class PlaybackStreamingResponse(StreamingResponse):
    """Release media streams when a browser or server cancels a response."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        except CancelledError:
            await _close_async_iterator(self.body_iterator)
            return


class _AsyncClosable(Protocol):
    """An async iterator that can release an interrupted media transfer."""

    async def aclose(self) -> None: ...


async def _close_async_iterator(iterator: AsyncIterable[object]) -> None:
    """Close an async generator when an ASGI response ends before its final byte."""

    if hasattr(iterator, "aclose"):
        await cast(_AsyncClosable, iterator).aclose()


@app.post("/kanvas/actions/items/{item_id}/download", include_in_schema=False)
async def create_item_download(item_id: int, request: Request) -> Response:
    """Create an owned, selected-version grant from a CSRF-protected native form."""

    profile = await require_profile(request)
    if item_id <= 0:
        raise HTTPException(status_code=422, detail="item_id must be positive.")
    form = await request.form()
    if not valid_download_csrf_token(request, form_value(form, "csrf_token")):
        raise HTTPException(status_code=403, detail="Download request could not be verified.")
    try:
        grant = await KanvasKatalogService(runtime.settings, profile.user.id).create_download_grant(
            item_id, form_integer(form, "media_file_id")
        )
    except KatalogClientError as error:
        return katalog_data_error(error, "Download is unavailable.")
    return RedirectResponse(
        _download_grant_location(grant.token),
        status_code=303,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


def _download_grant_location(grant_token: str) -> str:
    """Prefer a browser-reachable Katalog capability host when one is configured."""

    download_public_url = runtime.settings.download_public_url
    if download_public_url is None:
        return f"/kanvas/downloads/{grant_token}"
    return f"{str(download_public_url).rstrip('/')}/api/v1/download-grants/{grant_token}"


@app.api_route(
    "/kanvas/downloads/{grant_token}",
    methods=["GET", "HEAD"],
    include_in_schema=False,
)
async def download_grant(grant_token: str, request: Request) -> Response:
    """Proxy a short-lived opaque download grant without creating browser-side state."""

    catalogue = await create_katalog_client(runtime.settings, client_factory=KatalogClient)
    method: Literal["GET", "HEAD"] = "HEAD" if request.method == "HEAD" else "GET"
    transfer_context = catalogue.open_download_grant(
        grant_token,
        range_header=request.headers.get("range"),
        if_none_match=request.headers.get("if-none-match"),
        if_range=request.headers.get("if-range"),
        method=method,
    )
    try:
        transfer = await transfer_context.__aenter__()
    except ValueError as error:
        await catalogue.close()
        raise HTTPException(status_code=404, detail="Download is unavailable.") from error
    except KatalogClientError as error:
        await catalogue.close()
        return katalog_data_error(error, "Download is unavailable.")

    async def release_download_transfer() -> None:
        try:
            await transfer_context.__aexit__(None, None, None)
        finally:
            await catalogue.close()

    headers = _download_response_headers(transfer.headers)
    if request.method == "HEAD":
        await release_download_transfer()
        return Response(status_code=transfer.status_code, headers=headers)

    async def stream() -> AsyncIterator[bytes]:
        try:
            async for chunk in transfer.chunks:
                yield chunk
        except KatalogClientError as error:
            _LOGGER.warning("Browser download ended early: %s", error)
            raise
        finally:
            await release_download_transfer()

    return PlaybackStreamingResponse(stream(), status_code=transfer.status_code, headers=headers)


@app.api_route(
    "/kanvas/playback/sessions/{session_id}/entries/{entry_position}/media",
    methods=["GET", "HEAD"],
    include_in_schema=False,
)
async def playback_media(session_id: str, entry_position: int, request: Request) -> Response:
    """Proxy one owned Katalog media capability through Kanvas's browser session."""

    profile = await require_profile(request)
    try:
        session = await KanvasPlaybackService(runtime.settings, profile.user.id).playback_session(
            session_id
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Playback media is unavailable.") from error
    except KatalogClientError as error:
        return katalog_data_error(error, "Playback media is unavailable.")
    entry = session.current_item
    if entry is None or entry.position != entry_position:
        raise HTTPException(status_code=404, detail="Playback media is unavailable.")
    mode, audio_stream_index = _requested_playback_delivery(request)
    if not _valid_playback_delivery(entry, mode, audio_stream_index):
        raise HTTPException(status_code=404, detail="Playback media is unavailable.")
    start_seconds = _requested_stream_start_seconds(request, entry.duration_seconds)
    if mode is PlaybackMode.DIRECT and start_seconds > 0:
        raise HTTPException(status_code=422, detail="Direct playback does not use a stream start.")
    if mode is not PlaybackMode.DIRECT:
        if request.method == "HEAD":
            return Response(headers={"Content-Type": "video/mp4", "Cache-Control": "no-store"})
        try:
            ffmpeg_stream = await start_fragmented_mp4(
                runtime.settings.ffmpeg_executable,
                urljoin(str(runtime.settings.katalog_url), entry.stream_url),
                audio_stream_index=audio_stream_index,
                transcode_audio=mode is PlaybackMode.AUDIO_TRANSCODE,
                start_seconds=start_seconds,
            )
        except FFmpegError as error:
            _LOGGER.warning("Browser FFmpeg stream could not start: %s", error)
            return JSONResponse(
                {"error": "Browser playback conversion is unavailable."}, status_code=503
            )

        async def ffmpeg_output() -> AsyncIterator[bytes]:
            try:
                async for chunk in ffmpeg_stream.chunks():
                    yield chunk
            except FFmpegError as error:
                _LOGGER.warning("Browser FFmpeg stream ended early: %s", error)

        return PlaybackStreamingResponse(
            ffmpeg_output(),
            headers={"Content-Type": "video/mp4", "Cache-Control": "no-store"},
        )

    catalogue = await create_katalog_client(runtime.settings, client_factory=KatalogClient)
    transfer_context = catalogue.open_stream_media(
        entry.stream_url, range_header=request.headers.get("range")
    )
    try:
        transfer = await transfer_context.__aenter__()
    except KatalogClientError as error:
        await catalogue.close()
        return katalog_data_error(error, "Playback media is unavailable.")
    if request.method == "HEAD":
        await transfer_context.__aexit__(None, None, None)
        await catalogue.close()
        return Response(
            status_code=transfer.status_code,
            headers=_stream_response_headers(transfer.headers),
        )

    async def stream() -> AsyncIterator[bytes]:
        try:
            async for chunk in transfer.chunks:
                yield chunk
        except KatalogClientError as error:
            _LOGGER.warning("Browser media stream ended early: %s", error)
            raise
        finally:
            await transfer_context.__aexit__(None, None, None)
            await catalogue.close()

    return PlaybackStreamingResponse(
        stream(),
        status_code=transfer.status_code,
        headers=_stream_response_headers(transfer.headers),
    )


@app.post(
    "/kanvas/playback/sessions/{session_id}/entries/{entry_position}/compatibility",
    include_in_schema=False,
)
async def playback_compatibility(
    session_id: str, entry_position: int, request: Request
) -> JSONResponse:
    """Choose a browser delivery mode from probe metadata and browser evidence."""

    profile = await require_profile(request)
    payload = await json_object(request)
    try:
        capabilities = BrowserPlaybackCapabilities.model_validate(payload)
        session = await KanvasPlaybackService(runtime.settings, profile.user.id).playback_session(
            session_id
        )
    except ValidationError:
        return invalid_action("Browser capability data is invalid.")
    except KatalogClientError as error:
        return katalog_data_error(error, "Playback media is unavailable.")
    except ValueError:
        return invalid_action("Playback session is invalid.")
    entry = session.current_item
    if entry is None or entry.position != entry_position:
        raise HTTPException(status_code=404, detail="Playback media is unavailable.")
    decision = classify_playback(
        entry,
        capabilities,
        selected_audio_stream_index=entry.selected_audio_stream_index,
    )
    if decision.mode is PlaybackMode.UNSUPPORTED:
        try:
            fallback_uri = await KanvasPlaybackService(
                runtime.settings, profile.user.id
            ).create_kestrel_fallback_uri(session)
        except KatalogClientError, ValueError:
            fallback_uri = None
        return JSONResponse(
            {"mode": decision.mode.value, "mediaUrl": None, "fallbackUri": fallback_uri}
        )
    if decision.audio_stream_index is None:
        raise HTTPException(status_code=500, detail="Playback decision is incomplete.")
    media_url = (
        f"/kanvas/playback/sessions/{session.id}/entries/{entry.position}/media"
        f"?mode={decision.mode.value}&audioStream={decision.audio_stream_index}"
    )
    return JSONResponse({"mode": decision.mode.value, "mediaUrl": media_url, "fallbackUri": None})


@app.put("/kanvas/playback/sessions/{session_id}/tracks", include_in_schema=False)
async def playback_tracks(session_id: str, request: Request) -> JSONResponse:
    """Persist one browser-only track choice on the current owned queue entry."""

    profile = await require_profile(request)
    payload = await json_object(request)
    try:
        selection = PlaybackSessionTrackSelection.model_validate(
            {
                "expected_entry_position": nonnegative_integer(payload, "entryPosition"),
                "audio_stream_index": nonnegative_integer(payload, "audioStream"),
                "subtitle_track_id": _optional_track_id(payload.get("subtitleTrack")),
                "subtitle_timing_offset_milliseconds": signed_integer(
                    payload, "subtitleOffsetMilliseconds"
                ),
                "subtitle_font_scale_percent": integer(payload, "subtitleFontScalePercent"),
                "subtitle_background": boolean(payload, "subtitleBackground"),
                "subtitle_shadow": boolean(payload, "subtitleShadow"),
                "subtitle_vertical_position": payload.get("subtitleVerticalPosition"),
            }
        )
        session = await KanvasPlaybackService(
            runtime.settings, profile.user.id
        ).select_playback_tracks(session_id, selection)
    except ValidationError:
        return invalid_action("Playback track selection is invalid.")
    except ValueError:
        return invalid_action("Playback session is invalid.")
    except KatalogClientError as error:
        return katalog_data_error(error, "Playback track selection could not be saved.")
    entry = session.current_item
    if entry is None:
        raise HTTPException(status_code=404, detail="Playback media is unavailable.")
    return JSONResponse(
        {
            "audioStream": entry.selected_audio_stream_index,
            "subtitleTrack": entry.selected_subtitle_track_id,
            "subtitleOffsetMilliseconds": entry.subtitle_timing_offset_milliseconds,
            "subtitleFontScalePercent": entry.subtitle_font_scale_percent,
            "subtitleBackground": entry.subtitle_background,
            "subtitleShadow": entry.subtitle_shadow,
            "subtitleVerticalPosition": entry.subtitle_vertical_position.value,
        }
    )


@app.get(
    "/kanvas/playback/sessions/{session_id}/entries/{entry_position}/subtitles/{track_id}",
    include_in_schema=False,
)
async def playback_subtitle(
    session_id: str, entry_position: int, track_id: str, request: Request
) -> Response:
    """Serve browser VTT or local-libass subtitle input from an owned playback session."""

    profile = await require_profile(request)
    try:
        session = await KanvasPlaybackService(runtime.settings, profile.user.id).playback_session(
            session_id
        )
    except (KatalogClientError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Playback subtitle is unavailable.") from error
    entry = session.current_item
    if entry is None or entry.position != entry_position:
        raise HTTPException(status_code=404, detail="Playback subtitle is unavailable.")
    track = next(
        (candidate for candidate in entry.subtitle_tracks if candidate.id == track_id), None
    )
    if track is None or track.format is PlaybackSubtitleFormat.UNSUPPORTED:
        raise HTTPException(status_code=404, detail="Playback subtitle is unavailable.")
    offset_seconds = _requested_subtitle_offset_seconds(request, entry.duration_seconds)
    timing_offset_seconds = _requested_subtitle_timing_offset_seconds(
        request, entry.subtitle_timing_offset_milliseconds
    )
    if track.source is PlaybackSubtitleSource.SIDECAR:
        if track.content_url is None:
            raise HTTPException(status_code=404, detail="Playback subtitle is unavailable.")
        if (
            track.format is PlaybackSubtitleFormat.WEBVTT
            and _is_webvtt_track(track.codec)
            and offset_seconds == 0
            and timing_offset_seconds == 0
        ):
            return await _direct_webvtt_sidecar(track.content_url)
        content = await _sidecar_subtitle_content(track.content_url)
    else:
        content = await _embedded_subtitle_content(entry, track.id, track.format)
    if track.format is PlaybackSubtitleFormat.ASS:
        return Response(
            content=content,
            media_type="text/x-ssa",
            headers=_subtitle_cache_headers(),
        )
    try:
        return Response(
            content=as_webvtt(
                content,
                source_is_webvtt=_is_webvtt_track(track.codec),
                offset_seconds=offset_seconds,
                timing_offset_seconds=timing_offset_seconds,
            ),
            media_type="text/vtt",
            headers=_subtitle_cache_headers(),
        )
    except SubtitleConversionError as error:
        raise HTTPException(
            status_code=422, detail="Playback subtitle cannot be converted."
        ) from error


@app.get(
    "/kanvas/playback/sessions/{session_id}/entries/{entry_position}/fonts/{font_id}",
    include_in_schema=False,
)
async def playback_subtitle_font(
    session_id: str, entry_position: int, font_id: str, request: Request
) -> Response:
    """Serve one bounded embedded ASS font through its owning playback session."""

    profile = await require_profile(request)
    try:
        session = await KanvasPlaybackService(runtime.settings, profile.user.id).playback_session(
            session_id
        )
    except (KatalogClientError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Playback font is unavailable.") from error
    entry = session.current_item
    if entry is None or entry.position != entry_position:
        raise HTTPException(status_code=404, detail="Playback font is unavailable.")
    font = next(
        (candidate for candidate in entry.subtitle_font_attachments if candidate.id == font_id),
        None,
    )
    if font is None:
        raise HTTPException(status_code=404, detail="Playback font is unavailable.")
    content = await _embedded_subtitle_font_content(entry, font)
    return Response(
        content=content,
        media_type=_SUBTITLE_FONT_CONTENT_TYPES[font.format],
        headers={"Cache-Control": "no-store"},
    )


@app.post("/kanvas/playback/sessions/{session_id}/kestrel", include_in_schema=False)
async def playback_kestrel_fallback(session_id: str, request: Request) -> JSONResponse:
    """Expose Kestrel only after an owned browser session needs a richer renderer."""

    profile = await require_profile(request)
    try:
        service = KanvasPlaybackService(runtime.settings, profile.user.id)
        fallback_uri = await service.create_kestrel_fallback_uri(
            await service.playback_session(session_id)
        )
    except (KatalogClientError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Kestrel fallback is unavailable.") from error
    return JSONResponse({"fallbackUri": fallback_uri})


@app.put("/kanvas/playback/sessions/{session_id}/progress", include_in_schema=False)
async def playback_progress(session_id: str, request: Request) -> Response:
    """Persist a throttled browser progress sample for its owning profile."""

    profile = await require_profile(request)
    payload = await json_object(request)
    try:
        update = SessionProgressUpdate.model_validate(
            {
                "position_seconds": payload.get("positionSeconds"),
                "seek": payload.get("seek", False),
                "expected_entry_position": (
                    nonnegative_integer(payload, "entryPosition")
                    if "entryPosition" in payload
                    else None
                ),
            }
        )
        await KanvasPlaybackService(runtime.settings, profile.user.id).report_playback_progress(
            session_id, update
        )
    except ValidationError:
        return invalid_action("Playback progress is invalid.")
    except ValueError:
        return invalid_action("Playback session is invalid.")
    except KatalogClientError as error:
        if error.kind is KatalogClientErrorKind.NOT_FOUND:
            return Response(status_code=204)
        return katalog_data_error(error, "Playback progress could not be saved.")
    return Response(status_code=204)


@app.post("/kanvas/playback/sessions/{session_id}/complete", include_in_schema=False)
async def complete_playback(session_id: str, request: Request) -> JSONResponse:
    """Complete the current browser entry and return its next active entry when available."""

    profile = await require_profile(request)
    try:
        payload = await json_object(request)
        entry_position = nonnegative_integer(payload, "entryPosition")
        next_session = await KanvasPlaybackService(
            runtime.settings, profile.user.id
        ).complete_playback_entry(session_id, entry_position)
    except ValueError:
        return invalid_action("Playback session is invalid.")
    except KatalogClientError as error:
        return katalog_data_error(error, "Playback completion could not be saved.")
    next_item = (
        next_session.current_item if next_session.current_entry_position > entry_position else None
    )
    next_url = (
        f"/item/{next_item.item_id}?playbackSession={session_id}" if next_item is not None else None
    )
    return JSONResponse(
        BrowserPlaybackCompletionView(
            nextEntry=(
                BrowserPlaybackEntryView.from_entry(next_item) if next_item is not None else None
            ),
            nextUrl=next_url,
        ).model_dump(by_alias=True, mode="json")
    )


@app.post("/kanvas/playback/sessions/{session_id}/complete-current", include_in_schema=False)
async def complete_current_playback(session_id: str, request: Request) -> Response:
    """Explicitly complete the current browser entry without advancing its queue."""

    profile = await require_profile(request)
    try:
        payload = await json_object(request)
        entry_position = nonnegative_integer(payload, "entryPosition")
        await KanvasPlaybackService(
            runtime.settings, profile.user.id
        ).complete_current_playback_entry(session_id, entry_position)
    except ValueError:
        return invalid_action("Playback session is invalid.")
    except KatalogClientError as error:
        return katalog_data_error(error, "Playback completion could not be saved.")
    return Response(status_code=204)


def _stream_response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Preserve range headers while preventing stale session-scoped media reuse."""

    response_headers = {
        name: value for name, value in headers.items() if name.casefold() != "cache-control"
    }
    response_headers["Cache-Control"] = "no-store"
    return response_headers


def _download_response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Add capability-URL protections to a non-cacheable attachment response."""

    response_headers = _stream_response_headers(headers)
    response_headers["Referrer-Policy"] = "no-referrer"
    return response_headers


def _subtitle_cache_headers() -> dict[str, str]:
    """Keep immutable, session-scoped subtitle variants in the private browser cache."""

    return {"Cache-Control": _SUBTITLE_CACHE_CONTROL, "Vary": "Cookie"}


def _requested_playback_delivery(request: Request) -> tuple[PlaybackMode, int]:
    """Parse the small, server-validated delivery selector issued by Kanvas."""

    raw_mode = request.query_params.get("mode", PlaybackMode.DIRECT.value)
    try:
        mode = PlaybackMode(raw_mode)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Playback delivery mode is invalid.") from error
    raw_audio_index = request.query_params.get("audioStream", "0")
    if not raw_audio_index.isdecimal():
        raise HTTPException(status_code=422, detail="Playback audio stream is invalid.")
    audio_stream_index = int(raw_audio_index)
    if audio_stream_index > 63:
        raise HTTPException(status_code=422, detail="Playback audio stream is invalid.")
    return mode, audio_stream_index


def _requested_stream_start_seconds(request: Request, duration_seconds: float | None) -> float:
    """Parse one bounded generated-stream start position from a browser request."""

    value = request.query_params.get("startSeconds")
    if value is None:
        return 0.0
    try:
        start_seconds = float(value)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Playback stream start is invalid.") from error
    if not isfinite(start_seconds) or start_seconds < 0:
        raise HTTPException(status_code=422, detail="Playback stream start is invalid.")
    if duration_seconds is not None and start_seconds > duration_seconds:
        raise HTTPException(status_code=422, detail="Playback stream start exceeds media duration.")
    return start_seconds


def _requested_subtitle_offset_seconds(request: Request, duration_seconds: float | None) -> float:
    """Parse the generated media's absolute offset used to realign browser subtitles."""

    value = request.query_params.get("offsetSeconds")
    if value is None:
        return 0.0
    try:
        offset_seconds = float(value)
    except ValueError as error:
        raise HTTPException(
            status_code=422, detail="Playback subtitle offset is invalid."
        ) from error
    if not isfinite(offset_seconds) or offset_seconds < 0:
        raise HTTPException(status_code=422, detail="Playback subtitle offset is invalid.")
    if duration_seconds is not None and offset_seconds > duration_seconds:
        raise HTTPException(
            status_code=422, detail="Playback subtitle offset exceeds media duration."
        )
    return offset_seconds


def _requested_subtitle_timing_offset_seconds(request: Request, default_milliseconds: int) -> float:
    """Read a bounded subtitle timing adjustment, defaulting to the saved session value."""

    value = request.query_params.get("timingOffsetMilliseconds")
    if value is None:
        return default_milliseconds / 1_000
    try:
        timing_offset_milliseconds = int(value)
    except ValueError as error:
        raise HTTPException(
            status_code=422, detail="Playback subtitle timing offset is invalid."
        ) from error
    if (
        str(timing_offset_milliseconds) != value
        or abs(timing_offset_milliseconds) > MAX_SUBTITLE_TIMING_OFFSET_MILLISECONDS
    ):
        raise HTTPException(status_code=422, detail="Playback subtitle timing offset is invalid.")
    return timing_offset_milliseconds / 1_000


def _optional_track_id(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError("Playback subtitle track is invalid.")
    if (
        not (value.startswith("embedded-") or value.startswith("sidecar-"))
        or not value.partition("-")[2].isdecimal()
    ):
        raise ValueError("Playback subtitle track is invalid.")
    return value


def _is_webvtt_track(codec: str | None) -> bool:
    return (codec or "").casefold() in {"vtt", "webvtt"}


async def _direct_webvtt_sidecar(subtitle_url: str) -> PlaybackStreamingResponse:
    """Proxy a VTT sidecar byte-for-byte when no generated-stream offset is needed."""

    catalogue = await create_katalog_client(runtime.settings, client_factory=KatalogClient)
    transfer_context = catalogue.open_stream_subtitle(subtitle_url)
    try:
        transfer = await transfer_context.__aenter__()
    except KatalogClientError:
        await catalogue.close()
        raise HTTPException(status_code=404, detail="Playback subtitle is unavailable.") from None

    async def stream() -> AsyncIterator[bytes]:
        try:
            async for chunk in transfer.chunks:
                yield chunk
        finally:
            await transfer_context.__aexit__(None, None, None)
            await catalogue.close()

    headers = _stream_response_headers(transfer.headers)
    headers["Content-Type"] = "text/vtt"
    headers.update(_subtitle_cache_headers())
    return PlaybackStreamingResponse(stream(), status_code=transfer.status_code, headers=headers)


async def _sidecar_subtitle_content(subtitle_url: str) -> bytes:
    """Read one bounded sidecar through Katalog's opaque capability."""

    catalogue = await create_katalog_client(runtime.settings, client_factory=KatalogClient)
    try:
        async with catalogue.open_stream_subtitle(subtitle_url) as transfer:
            return await _bounded_subtitle_bytes(transfer.chunks)
    finally:
        await catalogue.close()


async def _embedded_subtitle_content(
    entry: PlaybackPlanEntry, track_id: str, subtitle_format: PlaybackSubtitleFormat
) -> bytes:
    """Extract embedded text/ASS tracks only; video is never decoded or burned in."""

    raw_index = track_id.removeprefix("embedded-")
    if not raw_index.isdecimal():
        raise HTTPException(status_code=404, detail="Playback subtitle is unavailable.")
    try:
        extracted = await start_subtitle_extract(
            runtime.settings.ffmpeg_executable,
            urljoin(str(runtime.settings.katalog_url), entry.stream_url),
            subtitle_stream_index=int(raw_index),
            ass=subtitle_format is PlaybackSubtitleFormat.ASS,
        )
        return await _bounded_subtitle_bytes(extracted.chunks())
    except FFmpegError as error:
        _LOGGER.warning("Browser subtitle extraction failed: %s", error)
        raise HTTPException(
            status_code=503, detail="Playback subtitle conversion is unavailable."
        ) from error


async def _embedded_subtitle_font_content(
    entry: PlaybackPlanEntry, font: PlaybackSubtitleFontAttachment
) -> bytes:
    """Extract a previously scanned font attachment, bounded before browser delivery."""

    try:
        extracted = await start_font_attachment_extract(
            runtime.settings.ffmpeg_executable,
            urljoin(str(runtime.settings.katalog_url), entry.stream_url),
            stream_index=font.stream_index,
        )
        return await _bounded_bytes(
            extracted.chunks(),
            maximum_bytes=_MAX_BROWSER_SUBTITLE_FONT_BYTES,
            too_large_detail="Playback subtitle font is too large.",
        )
    except FFmpegError as error:
        _LOGGER.warning("Browser subtitle font extraction failed: %s", error)
        raise HTTPException(
            status_code=503, detail="Playback font extraction is unavailable."
        ) from error


async def _bounded_subtitle_bytes(chunks: AsyncIterator[bytes]) -> bytes:
    """Keep subtitle conversion memory bounded even for malformed sidecar files."""

    return await _bounded_bytes(
        chunks,
        maximum_bytes=_MAX_BROWSER_SUBTITLE_BYTES,
        too_large_detail="Playback subtitle is too large.",
    )


async def _bounded_bytes(
    chunks: AsyncIterator[bytes], *, maximum_bytes: int, too_large_detail: str
) -> bytes:
    """Materialise a small FFmpeg result without allowing unbounded allocation."""

    content = bytearray()
    async for chunk in chunks:
        content.extend(chunk)
        if len(content) > maximum_bytes:
            raise HTTPException(status_code=422, detail=too_large_detail)
    return bytes(content)


def _valid_playback_delivery(
    entry: PlaybackPlanEntry, mode: PlaybackMode, audio_stream_index: int
) -> bool:
    """Keep query values from widening the server's no-video-transcode boundary."""

    video_streams = entry.video_streams
    audio_streams = entry.audio_streams
    if not video_streams:
        return mode is PlaybackMode.DIRECT and audio_stream_index == 0
    if len(video_streams) != 1 or audio_stream_index >= len(audio_streams):
        return False
    video_codec = (video_streams[0].codec or "").casefold()
    audio_codec = (audio_streams[audio_stream_index].codec or "").casefold()
    if video_codec not in {"h264", "avc", "avc1", "hevc", "h265", "hev1", "hvc1"}:
        return False
    if mode is PlaybackMode.DIRECT:
        return entry.container == "isobmff" and audio_stream_index == 0 and audio_codec == "aac"
    if mode is PlaybackMode.REMUX:
        return audio_codec == "aac" and (entry.container != "isobmff" or audio_stream_index != 0)
    return mode is PlaybackMode.AUDIO_TRANSCODE and audio_codec != "aac"

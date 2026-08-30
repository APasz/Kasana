"""Safe browser payloads used to transition an active playback player."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from kasana.katalog.public import (
    MAX_SUBTITLE_TIMING_OFFSET_MILLISECONDS,
    PlaybackPlanEntry,
    PlaybackSubtitleFormat,
    PlaybackSubtitleVerticalPosition,
)


class BrowserPlaybackAudioTrackView(BaseModel):
    """One displayable audio track without a media location."""

    model_config = ConfigDict(frozen=True)

    codec: str | None = Field(default=None, max_length=100)
    language: str | None = Field(default=None, max_length=32)
    title: str | None = Field(default=None, max_length=1_000)


class BrowserPlaybackSubtitleTrackView(BaseModel):
    """One displayable subtitle track without its source URL."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(pattern=r"^(?:embedded|sidecar)-\d+$")
    codec: str | None = Field(default=None, max_length=100)
    default: bool = False
    forced: bool = False
    format: PlaybackSubtitleFormat
    language: str | None = Field(default=None, max_length=32)
    title: str | None = Field(default=None, max_length=1_000)


class BrowserPlaybackEntryView(BaseModel):
    """The safe entry state required to continue playback without navigating away."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    position: int = Field(ge=0)
    item_id: int = Field(gt=0, alias="itemId")
    display_title: str = Field(min_length=1, max_length=1_000, alias="displayTitle")
    fullscreen_title: str = Field(min_length=1, max_length=2_003, alias="fullscreenTitle")
    special_info: str | None = Field(default=None, min_length=1, max_length=80, alias="specialInfo")
    duration_seconds: float | None = Field(default=None, ge=0, alias="durationSeconds")
    saved_resume_position_seconds: float = Field(ge=0, alias="savedResumePositionSeconds")
    audio_streams: tuple[BrowserPlaybackAudioTrackView, ...] = Field(
        default=(), alias="audioStreams", max_length=64
    )
    subtitle_tracks: tuple[BrowserPlaybackSubtitleTrackView, ...] = Field(
        default=(), alias="subtitleTracks", max_length=256
    )
    subtitle_font_ids: tuple[str, ...] = Field(default=(), alias="subtitleFontIds", max_length=64)
    selected_audio_stream_index: int = Field(ge=0, alias="selectedAudioStream")
    selected_subtitle_track_id: str | None = Field(
        default=None, alias="selectedSubtitleTrack", pattern=r"^(?:embedded|sidecar)-\d+$"
    )
    subtitle_timing_offset_milliseconds: int = Field(
        ge=-MAX_SUBTITLE_TIMING_OFFSET_MILLISECONDS,
        le=MAX_SUBTITLE_TIMING_OFFSET_MILLISECONDS,
        alias="subtitleTimingOffsetMilliseconds",
    )
    subtitle_font_scale_percent: int = Field(
        ge=75, le=200, multiple_of=25, alias="subtitleFontScalePercent"
    )
    subtitle_background: bool = Field(alias="subtitleBackground")
    subtitle_shadow: bool = Field(alias="subtitleShadow")
    subtitle_vertical_position: PlaybackSubtitleVerticalPosition = Field(
        alias="subtitleVerticalPosition"
    )

    @classmethod
    def from_entry(cls, entry: PlaybackPlanEntry) -> BrowserPlaybackEntryView:
        """Project a Katalog entry to the fields the browser player may receive."""

        return cls(
            position=entry.position,
            itemId=entry.item_id,
            displayTitle=entry.display_title,
            fullscreenTitle=_fullscreen_title(entry),
            specialInfo=entry.context_label,
            durationSeconds=entry.duration_seconds,
            savedResumePositionSeconds=entry.saved_resume_position_seconds,
            audioStreams=tuple(
                BrowserPlaybackAudioTrackView(
                    codec=stream.codec,
                    language=stream.language,
                    title=stream.title,
                )
                for stream in entry.audio_streams
            ),
            subtitleTracks=tuple(
                BrowserPlaybackSubtitleTrackView(
                    id=track.id,
                    codec=track.codec,
                    default=track.default,
                    forced=track.forced,
                    format=track.format,
                    language=track.language,
                    title=track.title,
                )
                for track in entry.subtitle_tracks
            ),
            subtitleFontIds=tuple(font.id for font in entry.subtitle_font_attachments),
            selectedAudioStream=entry.selected_audio_stream_index,
            selectedSubtitleTrack=entry.selected_subtitle_track_id,
            subtitleTimingOffsetMilliseconds=entry.subtitle_timing_offset_milliseconds,
            subtitleFontScalePercent=entry.subtitle_font_scale_percent,
            subtitleBackground=entry.subtitle_background,
            subtitleShadow=entry.subtitle_shadow,
            subtitleVerticalPosition=entry.subtitle_vertical_position,
        )


def _fullscreen_title(entry: PlaybackPlanEntry) -> str:
    """Return the concise title shown at the top of the fullscreen player."""

    if entry.series_title is None:
        return entry.display_title
    return f"{entry.series_title} · {entry.display_title}"


class BrowserPlaybackCompletionView(BaseModel):
    """The active entry after completing a browser playback item."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    next_entry: BrowserPlaybackEntryView | None = Field(default=None, alias="nextEntry")
    next_url: str | None = Field(default=None, alias="nextUrl")

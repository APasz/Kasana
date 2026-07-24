"""Server-validated browser playback decisions derived from probe metadata."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from kasana.katalog.public import PlaybackPlanEntry


class PlaybackMode(StrEnum):
    """The finite set of browser delivery modes supported by Kanvas."""

    DIRECT = "direct"
    REMUX = "remux"
    AUDIO_TRANSCODE = "audio-transcode"
    UNSUPPORTED = "unsupported"


class BrowserMediaCapability(BaseModel):
    """One browser result collected from MediaCapabilities and canPlayType."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    content_type: str = Field(min_length=1, max_length=300)
    media_capabilities_supported: bool
    can_play_type: str = Field(max_length=20)

    @property
    def is_supported(self) -> bool:
        return self.media_capabilities_supported or self.can_play_type in {"maybe", "probably"}


class BrowserPlaybackCapabilities(BaseModel):
    """Bounded, untrusted browser capability observations."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    media: tuple[BrowserMediaCapability, ...] = Field(default=(), max_length=24)


class PlaybackCompatibilityDecision(BaseModel):
    """A safe delivery decision; original stream metadata remains authoritative."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: PlaybackMode
    audio_stream_index: int | None = Field(default=None, ge=0)


def classify_playback(
    entry: PlaybackPlanEntry,
    capabilities: BrowserPlaybackCapabilities,
    *,
    preferred_audio_language: str | None,
) -> PlaybackCompatibilityDecision:
    """Classify a probed original without offering full video transcoding.

    Browser observations can unlock HEVC only after server-side codec validation.
    They never make an unknown video codec playable.
    """

    if len(entry.video_streams) != 1:
        return PlaybackCompatibilityDecision(mode=PlaybackMode.UNSUPPORTED)
    video_codec = _codec(entry.video_streams[0].codec)
    if video_codec in {"h264", "avc", "avc1"}:
        pass
    elif video_codec in {"hevc", "h265", "hev1", "hvc1"} and _browser_supports_hevc(
        capabilities
    ):
        pass
    else:
        return PlaybackCompatibilityDecision(mode=PlaybackMode.UNSUPPORTED)

    audio_stream_index = _preferred_audio_stream_index(entry, preferred_audio_language)
    if audio_stream_index is None:
        return PlaybackCompatibilityDecision(mode=PlaybackMode.UNSUPPORTED)
    audio_codec = _codec(entry.audio_streams[audio_stream_index].codec)
    mode = (
        PlaybackMode.DIRECT
        if entry.container == "isobmff" and audio_codec == "aac" and audio_stream_index == 0
        else PlaybackMode.REMUX
        if audio_codec == "aac"
        else PlaybackMode.AUDIO_TRANSCODE
    )
    return PlaybackCompatibilityDecision(mode=mode, audio_stream_index=audio_stream_index)


def _preferred_audio_stream_index(
    entry: PlaybackPlanEntry, preferred_language: str | None
) -> int | None:
    if not entry.audio_streams:
        return None
    normalised_preference = _language(preferred_language)
    if normalised_preference is not None:
        for index, stream in enumerate(entry.audio_streams):
            if _language(stream.language) == normalised_preference:
                return index
    return 0


def _browser_supports_hevc(capabilities: BrowserPlaybackCapabilities) -> bool:
    return any(
        capability.is_supported
        and "video/mp4" in capability.content_type.casefold()
        and any(codec in capability.content_type.casefold() for codec in ("hev1", "hvc1"))
        for capability in capabilities.media
    )


def _codec(value: str | None) -> str:
    return value.casefold().strip() if value is not None else ""


def _language(value: str | None) -> str | None:
    if value is None:
        return None
    normalised = value.casefold().strip().replace("_", "-")
    return normalised.split("-", maxsplit=1)[0] or None

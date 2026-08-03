"""Inline browser playback card rendering for authenticated Kanvas sessions."""

from __future__ import annotations

from nicegui import ui

from kasana.katalog.public import PlaybackPlanEntry, PlaybackSessionResponse, PlaybackSubtitleTrack

_PLAYBACK_RATES: tuple[float, ...] = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)


def _player_control(label: str, action: str, accessible_name: str) -> None:
    """Render one semantic button handled by the browser player component."""

    with ui.element("button").classes("k-player__control").props(
        f'type="button" data-player-action="{action}" aria-label="{accessible_name}"'
    ):
        ui.html(label, tag="span").classes("k-player__control-label")


def _queue_entry_context(entry: PlaybackPlanEntry) -> str | None:
    """Return the concise series context shown beneath one queued title."""

    episode_label = (
        f"S{entry.season_number:02d} E{entry.episode_number:02d}"
        if entry.season_number is not None and entry.episode_number is not None
        else None
    )
    return " · ".join(part for part in (entry.series_title, episode_label) if part) or None


def _render_playback_queue(session: PlaybackSessionResponse) -> None:
    """Render a compact disclosure for entries following the current item."""

    queued_entries = tuple(
        entry for entry in session.entries if entry.position > session.current_entry_position
    )
    if not queued_entries:
        return
    next_entry = queued_entries[0]
    with ui.element("details").classes("k-playback-queue").props("data-player-queue"):
        count_label = "item" if len(queued_entries) == 1 else "items"
        with ui.element("summary").classes("k-playback-queue__summary").props(
            'aria-label="Show playback queue"'
        ):
            ui.label(f"Queue · {len(queued_entries)} {count_label}").classes(
                "k-playback-queue__heading"
            )
            with ui.element("div").classes("k-playback-queue__next"):
                ui.label(next_entry.display_title).classes("k-playback-queue__title")
                context = _queue_entry_context(next_entry)
                if context is not None:
                    ui.label(context).classes("k-playback-queue__context")
        with ui.element("ol").classes("k-playback-queue__entries"):
            for index, queued_entry in enumerate(queued_entries, start=1):
                with ui.element("li").classes("k-playback-queue__entry"):
                    ui.label(f"Up next · {index}").classes(
                        "k-playback-queue__state"
                    )
                    with ui.element("div").classes("k-playback-queue__details"):
                        ui.label(queued_entry.display_title).classes("k-playback-queue__title")
                        context = _queue_entry_context(queued_entry)
                        if context is not None:
                            ui.label(context).classes("k-playback-queue__context")


def _audio_track_label(entry: PlaybackPlanEntry, index: int) -> str:
    stream = entry.audio_streams[index]
    parts = [stream.language, stream.title, stream.codec]
    return " · ".join(part for part in parts if part) or f"Audio {index + 1}"


def _subtitle_track_label(track: PlaybackSubtitleTrack) -> str:
    parts = [track.language, track.title, track.codec]
    label = " · ".join(part for part in parts if part) or "Subtitle"
    flags = " ".join(
        flag for flag, enabled in (("Default", track.default), ("Forced", track.forced)) if enabled
    )
    return f"{label} · {flags}" if flags else label


def _render_track_menus(entry: PlaybackPlanEntry) -> None:
    """Render compact, server-authoritative track menus without leaking source URLs."""

    with ui.element("div").classes("k-player__track-menu").props(
        'data-player-audio-menu role="menu" hidden'
    ):
        ui.label("Audio").classes("k-player__menu-heading")
        with ui.element("div").props('data-player-audio-options'):
            for index, _stream in enumerate(entry.audio_streams):
                with ui.element("button").classes("k-player__track-option").props(
                    f'type="button" data-player-audio-stream="{index}" '
                    f'aria-pressed="{str(index == entry.selected_audio_stream_index).lower()}"'
                ):
                    ui.label(_audio_track_label(entry, index))
    with ui.element("div").classes("k-player__track-menu").props(
        'data-player-subtitle-menu role="menu" hidden'
    ):
        ui.label("Subtitles").classes("k-player__menu-heading")
        with ui.element("div").props('data-player-subtitle-options'):
            with ui.element("button").classes("k-player__track-option").props(
                f'type="button" data-player-subtitle-track="" '
                f'aria-pressed="{str(entry.selected_subtitle_track_id is None).lower()}"'
            ):
                ui.label("Off")
            for track in entry.subtitle_tracks:
                unsupported = (
                    " data-player-subtitle-unsupported"
                    if track.format.value == "unsupported"
                    else ""
                )
                with ui.element("button").classes("k-player__track-option").props(
                    f'type="button" data-player-subtitle-track="{track.id}" '
                    f'data-player-subtitle-format="{track.format.value}"{unsupported} '
                    f'aria-pressed="{str(track.id == entry.selected_subtitle_track_id).lower()}"'
                ):
                    ui.label(_subtitle_track_label(track))
        with ui.element("div").classes("k-player__subtitle-timing").props(
            'role="group" aria-label="Subtitle timing"'
        ):
            ui.label("Timing").classes("k-player__subtitle-timing-heading")
            with ui.element("button").classes("k-player__timing-option").props(
                'type="button" data-player-subtitle-timing-step="-500" '
                'aria-label="Show subtitles 0.5 seconds earlier"'
            ):
                ui.label("Earlier")
            ui.label(_subtitle_timing_label(entry.subtitle_timing_offset_milliseconds)).props(
                'data-player-subtitle-timing-label aria-live="polite"'
            )
            with ui.element("button").classes("k-player__timing-option").props(
                'type="button" data-player-subtitle-timing-step="500" '
                'aria-label="Show subtitles 0.5 seconds later"'
            ):
                ui.label("Later")
            with ui.element("button").classes("k-player__timing-option").props(
                'type="button" data-player-subtitle-timing-reset aria-label="Reset subtitle timing"'
            ):
                ui.label("Reset")
        with ui.element("div").classes("k-player__subtitle-appearance").props(
            'data-player-subtitle-appearance role="group" aria-label="WebVTT subtitle appearance"'
        ):
            ui.label("Appearance · WebVTT").classes("k-player__subtitle-timing-heading")
            with ui.element("button").classes("k-player__timing-option").props(
                'type="button" data-player-subtitle-font-scale-step="-25" '
                'aria-label="Use smaller subtitle text"'
            ):
                ui.label("Smaller")
            ui.label(f"{entry.subtitle_font_scale_percent}%").props(
                'data-player-subtitle-font-scale-label aria-live="polite"'
            )
            with ui.element("button").classes("k-player__timing-option").props(
                'type="button" data-player-subtitle-font-scale-step="25" '
                'aria-label="Use larger subtitle text"'
            ):
                ui.label("Larger")
            with ui.element("button").classes("k-player__timing-option").props(
                'type="button" data-player-subtitle-background '
                f'aria-pressed="{str(entry.subtitle_background).lower()}"'
            ):
                ui.label("Backdrop")
            with ui.element("button").classes("k-player__timing-option").props(
                'type="button" data-player-subtitle-shadow '
                f'aria-pressed="{str(entry.subtitle_shadow).lower()}"'
            ):
                ui.label("Shadow")
            with ui.element("div").classes("k-player__subtitle-position"):
                for position, label in (
                    ("author", "Author"),
                    ("top", "Top"),
                    ("middle", "Middle"),
                    ("bottom", "Bottom"),
                ):
                    with ui.element("button").classes("k-player__timing-option").props(
                        f'type="button" data-player-subtitle-position="{position}" '
                        "aria-pressed="
                        f'"{str(entry.subtitle_vertical_position.value == position).lower()}"'
                    ):
                        ui.label(label)


def _subtitle_timing_label(offset_milliseconds: int) -> str:
    """Format a positive-later subtitle offset compactly for the player menu."""

    return f"{offset_milliseconds / 1_000:+.1f}s"


def render_browser_playback_card(
    session: PlaybackSessionResponse,
    *,
    autoplay_on_resume: bool = False,
    play_on_load: bool = False,
) -> None:
    """Render one current session entry with custom browser playback controls."""

    entry = session.current_item
    if entry is None:
        raise ValueError("Playback sessions must contain a current media item.")
    duration_attribute = (
        f' duration-seconds="{entry.duration_seconds:g}"'
        if entry.duration_seconds is not None and entry.duration_seconds > 0
        else ""
    )
    with (
        ui.element("kanvas-playback-player")
        .classes("k-player")
        .props(
            f'session-id="{session.id}" entry-position="{entry.position}" '
            f'resume-position="{entry.saved_resume_position_seconds}" '
            f'autoplay-on-resume="{str(autoplay_on_resume).lower()}" '
            f'play-on-load="{str(play_on_load).lower()}" '
            f'subtitle-timing-offset-milliseconds="{entry.subtitle_timing_offset_milliseconds}"'
            f' subtitle-font-scale-percent="{entry.subtitle_font_scale_percent}"'
            f' subtitle-background="{str(entry.subtitle_background).lower()}"'
            f' subtitle-shadow="{str(entry.subtitle_shadow).lower()}"'
            f' subtitle-vertical-position="{entry.subtitle_vertical_position.value}"'
            f"{duration_attribute}"
        )
    ):
        ui.label("Loading player…").classes("k-player__status").props('aria-live="polite"')
        if session.skipped_unavailable_titles:
            ui.label(
                "Skipped unavailable entries: " + " · ".join(session.skipped_unavailable_titles)
            ).classes("k-player__warning").props('aria-live="polite"')
        ui.element("a").classes("k-player__kestrel").props(
            'data-player-kestrel hidden aria-live="polite"'
        )
        ui.element("video").classes("k-player__video").props(
            'playsinline preload="metadata"'
        )
        with ui.element("span").props('data-player-ass-fonts hidden'):
            for font in entry.subtitle_font_attachments:
                ui.element("span").props(f'data-player-ass-font="{font.id}"')
        with ui.element("div").classes("k-player__progress"):
            ui.label("0:00").classes("k-player__time k-player__bar-label").props(
                'data-player-current-time aria-live="off"'
            )
            ui.element("span").classes("k-player__buffered").props(
                'data-player-buffered aria-hidden="true"'
            )
            ui.element("input").classes("k-player__timeline").props(
                'type="range" min="0" max="0" value="0" step="0.1" '
                'data-player-timeline aria-label="Seek" disabled'
            )
            ui.label("-0:00").classes(
                "k-player__time k-player__time--remaining k-player__bar-label"
            ).props(
                'data-player-remaining-time aria-live="off"'
            )
        with ui.element("div").classes("k-player__details"):
            with ui.element("div").classes("k-player__controls").props(
                'aria-label="Playback controls"'
            ):
                with ui.element("div").classes("k-player__transport-controls"):
                    _player_control("-10s", "rewind", "Rewind 10 seconds")
                    _player_control("&#9654;", "toggle", "Play")
                    _player_control("&#8942;", "menu", "Playback settings")
                    _player_control("+10s", "forward", "Forward 10 seconds")
                with ui.element("div").classes("k-player__audio-controls"):
                    _player_control("&#128266;", "mute", "Mute")
                    _player_control("&#127911;", "audio", "Audio tracks")
                    _player_control("&#128172;", "subtitles", "Subtitle tracks")
                    ui.element("input").classes("k-player__volume").props(
                        'type="range" min="0" max="1" value="1" step="0.05" '
                        'data-player-volume aria-label="Volume"'
                    )
                    _player_control("&#9974;", "fullscreen", "Fullscreen")
        with ui.element("div").classes("k-player__context-menu").props(
            'data-player-context-menu role="menu" hidden'
        ):
            ui.label("Playback speed").classes("k-player__menu-heading")
            with ui.element("div").classes("k-player__speed-options"):
                for rate in _PLAYBACK_RATES:
                    with ui.element("button").classes("k-player__speed-option").props(
                        f'type="button" data-player-rate="{rate:g}" aria-pressed="false"'
                    ):
                        ui.html(f"{rate:g}x", tag="span")
            with ui.element("div").classes("k-player__context-option"):
                ui.element("input").props(
                    'type="checkbox" data-player-native-controls aria-label="Show browser controls"'
                )
                ui.html("Show browser controls", tag="span")
        _render_track_menus(entry)
    _render_playback_queue(session)

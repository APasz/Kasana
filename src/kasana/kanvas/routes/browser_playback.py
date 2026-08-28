"""Inline browser playback card rendering for authenticated Kanvas sessions."""

from __future__ import annotations

from enum import StrEnum
from html import escape

from nicegui import ui
from nicegui.element import Element

from kasana.kanvas.components.controls import IconName, icon_svg
from kasana.kanvas.viewmodels.playback import BrowserPlaybackEntryView
from kasana.katalog.public import (
    PlaybackPlanEntry,
    PlaybackSessionResponse,
    PlaybackSubtitleTrack,
)

_PLAYBACK_RATES: tuple[float, ...] = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)


class _PlayerControlAction(StrEnum):
    """Browser actions available from the custom player controls."""

    TOGGLE = "toggle"
    REWIND = "rewind"
    FORWARD = "forward"
    MENU = "menu"
    NEXT = "next"
    SUBTITLES = "subtitles"
    AUDIO = "audio"
    MUTE = "mute"
    THEATRE = "theatre"
    FULLSCREEN = "fullscreen"
    OVERFLOW = "overflow"


class _FullscreenFrameAlignment(StrEnum):
    """Physical placement choices for a contained fullscreen video frame."""

    CENTRED = "centred"
    START = "start"
    END = "end"


def _player_icon(icon: IconName, alternate_icon: IconName | None = None) -> None:
    """Render a player icon, optionally with a script-selectable alternate glyph."""

    with ui.element("span").classes("k-player__control-icon k-player__control-icon--default"):
        icon_svg(icon)
    if alternate_icon is not None:
        with ui.element("span").classes(
            "k-player__control-icon k-player__control-icon--alternate"
        ):
            icon_svg(alternate_icon)


def _player_action_button(
    classes: str,
    action: _PlayerControlAction,
    accessible_name: str,
    *,
    alternate_icon: IconName | None = None,
) -> Element:
    """Build one accessible player action shell for its SVG content."""

    escaped_name = escape(accessible_name, quote=True)
    button = ui.element("button").classes(classes)
    button.props(
        f'type="button" data-player-action="{action.value}" '
        f'aria-label="{escaped_name}" data-player-tooltip="{escaped_name}"'
        + (' data-player-icon-state="default"' if alternate_icon is not None else "")
    )
    if action is _PlayerControlAction.OVERFLOW:
        button.props('aria-haspopup="true" aria-expanded="false"')
    if action is _PlayerControlAction.THEATRE:
        button.props('aria-pressed="false"')
    return button


def _player_control(
    icon: IconName,
    action: _PlayerControlAction,
    accessible_name: str,
    *,
    alternate_icon: IconName | None = None,
    extra_classes: tuple[str, ...] = (),
) -> None:
    """Render one semantic button handled by the browser player component."""

    button = _player_action_button(
        " ".join(("k-player__control", *extra_classes)),
        action,
        accessible_name,
        alternate_icon=alternate_icon,
    )
    with button:
        _player_icon(icon, alternate_icon)


def _fullscreen_frame_alignment_option(
    alignment: _FullscreenFrameAlignment,
    label: str,
    icon: IconName,
) -> None:
    """Render one icon option in the fullscreen video-frame alignment control."""

    escaped_label = escape(label, quote=True)
    with ui.element("button").classes("k-player__frame-alignment-option").props(
        f'type="button" data-player-frame-alignment-option="{alignment.value}" '
        f'aria-label="{escaped_label}" '
        f'aria-pressed="{str(alignment is _FullscreenFrameAlignment.CENTRED).lower()}"'
    ):
        icon_svg(icon)


def _mobile_player_menu_option(
    icon: IconName,
    action: _PlayerControlAction,
    label: str,
    *,
    alternate_icon: IconName | None = None,
    dynamic_label: bool = False,
) -> None:
    """Render one labelled control inside the narrow-player overflow menu."""

    button = _player_action_button(
        "k-player__mobile-menu-option",
        action,
        label,
        alternate_icon=alternate_icon,
    )
    with button:
        _player_icon(icon, alternate_icon)
        visible_label = ui.label(label).classes("k-player__mobile-menu-option-label")
        if dynamic_label:
            visible_label.props(f'data-player-action-label="{action.value}"')


def _queued_entries(session: PlaybackSessionResponse) -> tuple[PlaybackPlanEntry, ...]:
    """Return entries scheduled after the session's current item."""

    return tuple(
        entry for entry in session.entries if entry.position > session.current_entry_position
    )


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

    queued_entries = _queued_entries(session)
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
            with ui.element("button").classes("k-button k-playback-queue__advance").props(
                'type="button" data-player-next aria-label="Play the next queue item"'
            ):
                ui.label("Play next").classes("k-button__label")
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
    browser_entry = BrowserPlaybackEntryView.from_entry(entry)
    has_queued_item = bool(_queued_entries(session))
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
        with ui.element("div").classes("k-player__fullscreen-info"):
            ui.label(browser_entry.fullscreen_title).classes("k-player__fullscreen-title").props(
                'data-player-fullscreen-title'
            )
            ui.label(browser_entry.special_info or "").classes(
                "k-player__fullscreen-special-info"
            ).props(
                "data-player-fullscreen-special-info"
                + (" hidden" if browser_entry.special_info is None else "")
            )
            ui.label("").classes("k-player__fullscreen-time").props(
                'data-player-fullscreen-time aria-label="Current time"'
            )
        with ui.element("div").classes("k-player__frame-alignment").props(
            'data-player-frame-alignment-controls role="group" '
            'aria-label="Video alignment" hidden'
        ):
            _fullscreen_frame_alignment_option(
                _FullscreenFrameAlignment.START, "Left", IconName.FRAME_ALIGN_START
            )
            _fullscreen_frame_alignment_option(
                _FullscreenFrameAlignment.CENTRED, "Centred", IconName.FRAME_ALIGN_CENTRE
            )
            _fullscreen_frame_alignment_option(
                _FullscreenFrameAlignment.END, "Right", IconName.FRAME_ALIGN_END
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
            ui.label("0:00").classes("k-player__timeline-preview").props(
                'data-player-timeline-preview aria-hidden="true" hidden'
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
                    _player_control(
                        IconName.REWIND,
                        _PlayerControlAction.REWIND,
                        "Rewind 10 seconds",
                    )
                    _player_control(
                        IconName.PLAY,
                        _PlayerControlAction.TOGGLE,
                        "Play",
                        alternate_icon=IconName.PAUSE,
                        extra_classes=("k-player__control--toggle",),
                    )
                    _player_control(
                        IconName.MORE,
                        _PlayerControlAction.MENU,
                        "Playback settings",
                        extra_classes=("k-player__control--settings",),
                    )
                    _player_control(
                        IconName.FORWARD,
                        _PlayerControlAction.FORWARD,
                        "Forward 10 seconds",
                    )
                    if has_queued_item:
                        _player_control(
                            IconName.NEXT,
                            _PlayerControlAction.NEXT,
                            "Play the next queue item",
                            extra_classes=("k-player__control--next",),
                        )
                    _player_control(
                        IconName.MORE,
                        _PlayerControlAction.OVERFLOW,
                        "More playback controls",
                        extra_classes=("k-player__control--overflow",),
                    )
                with ui.element("div").classes("k-player__audio-controls"):
                    _player_control(
                        IconName.SUBTITLES, _PlayerControlAction.SUBTITLES, "Subtitle tracks"
                    )
                    _player_control(IconName.AUDIO, _PlayerControlAction.AUDIO, "Audio tracks")
                    _player_control(
                        IconName.VOLUME,
                        _PlayerControlAction.MUTE,
                        "Mute",
                        alternate_icon=IconName.VOLUME_MUTED,
                    )
                    ui.element("input").classes("k-player__volume").props(
                        'type="range" min="0" max="1" value="1" step="0.05" '
                        'data-player-volume aria-label="Volume"'
                    )
                    _player_control(
                        IconName.THEATRE,
                        _PlayerControlAction.THEATRE,
                        "Theatre mode",
                        alternate_icon=IconName.THEATRE_EXIT,
                    )
                    _player_control(
                        IconName.FULLSCREEN,
                        _PlayerControlAction.FULLSCREEN,
                        "Fullscreen",
                        alternate_icon=IconName.FULLSCREEN_EXIT,
                    )
            with ui.element("div").classes("k-player__mobile-menu").props(
                'data-player-mobile-menu role="group" aria-label="More playback controls" hidden'
            ):
                _mobile_player_menu_option(
                    IconName.ADMINISTRATION,
                    _PlayerControlAction.MENU,
                    "Playback settings",
                )
                _mobile_player_menu_option(
                    IconName.SUBTITLES,
                    _PlayerControlAction.SUBTITLES,
                    "Subtitle tracks",
                )
                _mobile_player_menu_option(
                    IconName.AUDIO,
                    _PlayerControlAction.AUDIO,
                    "Audio tracks",
                )
                _mobile_player_menu_option(
                    IconName.VOLUME,
                    _PlayerControlAction.MUTE,
                    "Mute",
                    alternate_icon=IconName.VOLUME_MUTED,
                    dynamic_label=True,
                )
                _mobile_player_menu_option(
                    IconName.THEATRE,
                    _PlayerControlAction.THEATRE,
                    "Theatre mode",
                    alternate_icon=IconName.THEATRE_EXIT,
                    dynamic_label=True,
                )
                _mobile_player_menu_option(
                    IconName.FULLSCREEN,
                    _PlayerControlAction.FULLSCREEN,
                    "Fullscreen",
                    alternate_icon=IconName.FULLSCREEN_EXIT,
                    dynamic_label=True,
                )
                with ui.element("label").classes("k-player__mobile-volume"):
                    icon_svg(IconName.VOLUME)
                    ui.label("Volume").classes("k-player__mobile-volume-label")
                    ui.element("input").classes("k-player__volume").props(
                        'type="range" min="0" max="1" value="1" step="0.05" '
                        'data-player-mobile-volume aria-label="Volume"'
                    )
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
            if has_queued_item:
                with ui.element("label").classes("k-player__context-option").props(
                    "data-player-autoplay-next-option"
                ):
                    ui.element("input").props(
                        'type="checkbox" data-player-autoplay-next checked '
                        'aria-label="Autoplay next queue item"'
                    )
                    ui.html("Autoplay next item", tag="span")
        _render_track_menus(entry)
        ui.element("span").classes("k-player__tooltip").props(
            'data-player-tooltip-host aria-hidden="true" hidden'
        )
    _render_playback_queue(session)

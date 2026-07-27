"""Inline browser playback card rendering for authenticated Kanvas sessions."""

from __future__ import annotations

from nicegui import ui

from kasana.katalog.public import PlaybackPlanEntry, PlaybackSessionResponse

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
    with ui.element("details").classes("k-playback-queue"):
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


def render_browser_playback_card(session: PlaybackSessionResponse) -> None:
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
            f'resume-position="{entry.saved_resume_position_seconds}"{duration_attribute}'
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
            'autoplay playsinline preload="metadata"'
        )
        with ui.element("div").classes("k-player__progress"):
            ui.label("0:00").classes("k-player__time k-player__bar-label").props(
                'data-player-current-time aria-live="off"'
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
    _render_playback_queue(session)

"""Accessible native controls and keyboard/controller action mapping."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from html import escape
from typing import Any

from nicegui import ui
from nicegui.element import Element
from nicegui.elements.label import Label


class NavigationAction(StrEnum):
    """Actions shared by keyboard and browser gamepad support."""

    ACTIVATE = "activate"
    BACK = "back"
    FOCUS_SEARCH = "focus_search"
    MOVE_UP = "move_up"
    MOVE_DOWN = "move_down"
    MOVE_LEFT = "move_left"
    MOVE_RIGHT = "move_right"


class ButtonType(StrEnum):
    """Semantic HTML button types supported by Kanvas actions."""

    BUTTON = "button"
    SUBMIT = "submit"


class IconName(StrEnum):
    """Locally controlled SVG icons available to Kanvas components."""

    HOME = "home"
    LIBRARY = "library"
    COLLECTIONS = "collections"
    SEARCH = "search"
    ADMINISTRATION = "admin"
    INFO = "info"
    PLAY = "play"
    PAUSE = "pause"
    REWIND = "rewind"
    FORWARD = "forward"
    NEXT = "next"
    SUBTITLES = "subtitles"
    AUDIO = "audio"
    VOLUME = "volume"
    VOLUME_MUTED = "volume_muted"
    THEATRE = "theatre"
    THEATRE_EXIT = "theatre_exit"
    FRAME_ALIGN_START = "frame_align_start"
    FRAME_ALIGN_CENTRE = "frame_align_centre"
    FRAME_ALIGN_END = "frame_align_end"
    FULLSCREEN = "fullscreen"
    FULLSCREEN_EXIT = "fullscreen_exit"
    MORE = "more"
    CHECK = "check"
    BACK = "back"


_KEY_ACTIONS: dict[str, NavigationAction] = {
    "Enter": NavigationAction.ACTIVATE,
    " ": NavigationAction.ACTIVATE,
    "Escape": NavigationAction.BACK,
    "/": NavigationAction.FOCUS_SEARCH,
    "ArrowUp": NavigationAction.MOVE_UP,
    "ArrowDown": NavigationAction.MOVE_DOWN,
    "ArrowLeft": NavigationAction.MOVE_LEFT,
    "ArrowRight": NavigationAction.MOVE_RIGHT,
}


_ICON_PATHS: dict[IconName, str] = {
    IconName.HOME: (
        "M3 10.5 12 3l9 7.5v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 19.5z M9 21v-6h6v6"
    ),
    IconName.LIBRARY: "M4 4h16v16H4z M8 4v16 M12 4v16",
    IconName.COLLECTIONS: "M4 5h16v4H4z M4 15h16v4H4z M7 9v6 M17 9v6",
    IconName.SEARCH: "m20 20-4.5-4.5 M11 18a7 7 0 1 1 0-14 7 7 0 0 1 0 14z",
    IconName.ADMINISTRATION: (
        "M12 3v3 M12 18v3 M3 12h3 M18 12h3 M5.6 5.6l2.1 2.1 M16.3 16.3l2.1 2.1 "
        "M18.4 5.6l-2.1 2.1 M7.7 16.3l-2.1 2.1 M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0z"
    ),
    IconName.INFO: "M12 17v-6 M12 7.5v.01 M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z",
    IconName.PLAY: "M8 5v14l11-7z",
    IconName.PAUSE: "M8 5v14 M16 5v14",
    IconName.REWIND: "M20 5v14l-7-7z M11 5v14l-7-7z",
    IconName.FORWARD: "M4 5v14l7-7z M13 5v14l7-7z",
    IconName.NEXT: "M5 5v14l9-7z M16 5v14",
    IconName.SUBTITLES: "M4 5h16v11H8l-4 4z M8 9h8 M8 12h5",
    IconName.AUDIO: "M4 13a8 8 0 0 1 16 0v5h-3v-5 M4 13v5h3v-5",
    IconName.VOLUME: "M4 10h4l5-4v12l-5-4H4z M16 9a4 4 0 0 1 0 6 M18 6a8 8 0 0 1 0 12",
    IconName.VOLUME_MUTED: "M4 10h4l5-4v12l-5-4H4z M17 10l4 4 M21 10l-4 4",
    IconName.THEATRE: "M3 5h18v14H3z M7 9h10v6H7z",
    IconName.THEATRE_EXIT: "M3 5h18v14H3z M9 9h6v6H9z",
    IconName.FRAME_ALIGN_START: "M4 5h16 M4 9h11 M4 13h14 M4 17h8",
    IconName.FRAME_ALIGN_CENTRE: "M5 5h14 M7 9h10 M5 13h14 M7 17h10",
    IconName.FRAME_ALIGN_END: "M4 5h16 M9 9h11 M6 13h14 M12 17h8",
    IconName.FULLSCREEN: "M4 9V4h5 M15 4h5v5 M20 15v5h-5 M9 20H4v-5",
    IconName.FULLSCREEN_EXIT: "M9 4v5H4 M15 4v5h5 M20 15h-5v5 M4 15h5v5",
    IconName.MORE: "M12 5v2 M12 11v2 M12 17v2",
    IconName.CHECK: "m5 12 4 4L19 6",
    IconName.BACK: "m14 5-7 7 7 7",
}


@dataclass(frozen=True)
class ActionButton:
    """A native button and its mutable visible label."""

    element: Element
    label: Label

    def set_text(self, text: str) -> None:
        """Update the button label after a local optimistic state transition."""

        self.label.set_text(text)


def keyboard_action(key: str) -> NavigationAction | None:
    """Map a browser key to a deliberate Kanvas navigation action."""

    return _KEY_ACTIONS.get(key)


def action_button(
    label: str,
    handler: Callable[..., Any] | None = None,
    *,
    primary: bool = False,
    disabled: bool = False,
    button_type: ButtonType = ButtonType.BUTTON,
) -> ActionButton:
    """Build a square, semantic button without inheriting Quasar button styling."""

    classes = "k-button k-button--primary" if primary else "k-button"
    button = ui.element("button").classes(classes).props(f"type={button_type.value}")
    button.props(f'aria-label="{escape(label, quote=True)}"')
    if disabled:
        button.props("disabled")
    if handler is not None:
        button.on("click", handler)
    with button:
        visible_label = ui.label(label).classes("k-button__label")
    return ActionButton(button, visible_label)


def action_form_props(action: str) -> str:
    """Mark a same-origin native mutation form for shared toast-aware handling."""

    if (
        not action.startswith("/")
        or action.startswith("//")
        or "\\" in action
        or any(character.isspace() for character in action)
    ):
        msg = "Kanvas action forms must use an internal absolute path."
        raise ValueError(msg)
    return (
        f'method="post" action="{escape(action, quote=True)}" '
        'data-kanvas-action-form="true"'
    )


def icon_action(label: str, icon: IconName, handler: Callable[..., Any] | None = None) -> Element:
    """Build a labelled icon action with a persistent accessible name."""

    button = ui.element("button").classes("k-icon-action").props("type=button")
    escaped_label = escape(label, quote=True)
    button.props(f'aria-label="{escaped_label}" title="{escaped_label}"')
    if handler is not None:
        button.on("click", handler)
    with button:
        icon_svg(icon)
    return button


def icon_svg(name: IconName | str) -> None:
    """Render one locally controlled glyph as inline SVG."""

    try:
        icon = IconName(name)
    except ValueError as error:
        msg = f"Unknown Kanvas icon: {name}."
        raise ValueError(msg) from error
    path = _ICON_PATHS[icon]
    ui.html(f'<svg class="k-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="{path}" /></svg>')

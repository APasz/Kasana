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
    """Locally controlled icons available to Kanvas components."""

    HOME = "home"
    LIBRARY = "library"
    COLLECTIONS = "collections"
    SEARCH = "search"
    ADMINISTRATION = "admin"
    PLAY = "play"
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


def icon_action(
    label: str, icon: IconName, handler: Callable[..., Any] | None = None
) -> Element:
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
    """Render the five locally controlled navigation glyphs as inline SVG."""

    paths = {
        IconName.HOME: (
            "M3 10.5 12 3l9 7.5v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 "
            "1 3 19.5z M9 21v-6h6v6"
        ),
        IconName.LIBRARY: "M4 4h16v16H4z M8 4v16 M12 4v16",
        IconName.COLLECTIONS: "M4 5h16v4H4z M4 15h16v4H4z M7 9v6 M17 9v6",
        IconName.SEARCH: "m20 20-4.5-4.5 M11 18a7 7 0 1 1 0-14 7 7 0 0 1 0 14z",
        IconName.ADMINISTRATION: (
            "M12 3v3 M12 18v3 M3 12h3 M18 12h3 M5.6 5.6l2.1 2.1 M16.3 16.3l2.1 2.1 "
            "M18.4 5.6l-2.1 2.1 M7.7 16.3l-2.1 2.1 M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0z"
        ),
        IconName.PLAY: "M8 5v14l11-7z",
        IconName.CHECK: "m5 12 4 4L19 6",
        IconName.BACK: "m14 5-7 7 7 7",
    }
    try:
        icon = IconName(name)
    except ValueError as error:
        msg = f"Unknown Kanvas icon: {name}."
        raise ValueError(msg) from error
    path = paths[icon]
    ui.html(f'<svg class="k-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="{path}" /></svg>')

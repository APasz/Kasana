"""Compact horizontal media rails."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from nicegui import ui

from kasana.kanvas.components.poster import poster_card
from kasana.kanvas.components.typography import quiet_copy, section_title
from kasana.kanvas.viewmodels.home import HomeRailKind, MediaRailView
from kasana.kanvas.viewmodels.library import PosterAction


@dataclass(frozen=True)
class EmptyRailState:
    """Purposeful empty copy and optional next destination for one Home rail."""

    message: str
    action_label: str | None = None
    action_href: Literal["/library", "/collections"] | None = None

    def __post_init__(self) -> None:
        if (self.action_label is None) != (self.action_href is None):
            raise ValueError("An empty rail action requires both a label and destination.")


_EMPTY_RAIL_STATES: dict[HomeRailKind, EmptyRailState] = {
    HomeRailKind.GENERIC: EmptyRailState("Nothing here yet."),
    HomeRailKind.CONTINUE: EmptyRailState("Nothing to resume yet.", "Browse library", "/library"),
    HomeRailKind.ON_DECK: EmptyRailState(
        "No active watch orders yet.", "Open collections", "/collections"
    ),
    HomeRailKind.RECENTLY_ADDED: EmptyRailState(
        "Your library is empty.", "Browse library", "/library"
    ),
}

_RAIL_POSTER_ACTIONS: dict[HomeRailKind, PosterAction] = {
    HomeRailKind.CONTINUE: PosterAction.RESUME,
    HomeRailKind.ON_DECK: PosterAction.PLAY_NEXT,
}


def media_rail(rail: MediaRailView) -> None:
    """Render an input-friendly rail whose scrollbars are intentionally hidden."""

    with (
        ui.element("section")
        .classes(f"k-rail k-rail--{rail.kind.value}")
        .props(f"aria-label={json.dumps(rail.title)}")
    ):
        with ui.element("div").classes("k-rail__heading"):
            section_title(rail.title)
            if len(rail.posters) > 1:
                _rail_scroll_controls(rail.title)
        if not rail.posters:
            _empty_rail(rail.kind)
            return
        action = _RAIL_POSTER_ACTIONS.get(rail.kind)
        with (
            ui.element("div")
            .classes("k-rail__viewport")
            .props('tabindex="0" data-kanvas-rail-viewport="true"')
        ):
            for poster in rail.posters:
                poster_card(poster, action=action)


def _empty_rail(kind: HomeRailKind) -> None:
    """Render copy that explains the empty rail's next useful action."""

    state = _EMPTY_RAIL_STATES[kind]
    with ui.element("div").classes("k-rail__empty"):
        quiet_copy(state.message)
        if state.action_label is not None and state.action_href is not None:
            with (
                ui.element("a").classes("k-rail__empty-action").props(f'href="{state.action_href}"')
            ):
                ui.label(state.action_label)


def _rail_scroll_controls(title: str) -> None:
    """Render keyboard-accessible controls for a horizontally scrollable rail."""

    with ui.element("div").classes("k-rail__controls").props("hidden"):
        ui.element("button").classes("k-rail__scroll k-rail__scroll--previous").props(
            'type="button" data-kanvas-rail-scroll="previous" '
            f"aria-label={json.dumps(f'Scroll {title} backward')}"
        )
        ui.element("button").classes("k-rail__scroll k-rail__scroll--next").props(
            'type="button" data-kanvas-rail-scroll="next" '
            f"aria-label={json.dumps(f'Scroll {title} forward')}"
        )

"""Safe mounting boundary for browser-owned Kanvas custom elements."""

from __future__ import annotations

import re
from collections.abc import Mapping
from enum import StrEnum

from nicegui import ui
from nicegui.element import Element


class BrowserComponent(StrEnum):
    """Custom elements implemented by the shared Kanvas browser bundle."""

    ADMINISTRATION = "kanvas-administration"
    COLLECTION_GRID = "kanvas-collection-grid"
    ITEM_EDITOR = "kanvas-item-editor"
    ONBOARDING = "kanvas-onboarding"
    ITEM_PICKER = "kanvas-item-picker"
    POSTER_GRID = "kanvas-poster-grid"
    WATCH_ORDER_LIST = "kanvas-watch-order-list"


type BrowserAttribute = str | int | bool

_ATTRIBUTE_NAME = re.compile(r"^[a-z][a-z0-9-]*$")


def mount_browser_component(
    component: BrowserComponent, attributes: Mapping[str, BrowserAttribute]
) -> Element:
    """Mount one typed browser component as a native NiceGUI element.

    This avoids the dynamic HTML wrapper lifecycle entirely. Attribute names are
    validated before their values are passed to NiceGUI's property parser.
    """

    if any(not _valid_attribute_name(name) for name in attributes):
        msg = "Browser component attributes must use lower-case kebab-case names."
        raise ValueError(msg)
    rendered_attributes = " ".join(
        f"{name}={_attribute_value(value)!r}" for name, value in attributes.items()
    )
    return ui.element(component.value).props(rendered_attributes)


def _attribute_value(value: BrowserAttribute) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


def _valid_attribute_name(name: str) -> bool:
    return _ATTRIBUTE_NAME.fullmatch(name) is not None

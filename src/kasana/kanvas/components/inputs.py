"""Native Kanvas form controls with one consistent accessible structure."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from nicegui import ui
from nicegui.element import Element


@dataclass(frozen=True)
class SelectOption:
    """One safe native select option."""

    value: str
    label: str


def text_input(
    *,
    name: str,
    input_type: str = "text",
    value: str | None = None,
    placeholder: str | None = None,
    aria_label: str,
    classes: str = "",
    shell_classes: str = "",
    autofocus: bool = False,
) -> Element:
    """Render a styled native input inside its focus-border shell."""

    attributes = [
        f'name="{escape(name, quote=True)}"',
        f'type="{escape(input_type, quote=True)}"',
        f'aria-label="{escape(aria_label, quote=True)}"',
    ]
    if value is not None:
        attributes.append(f'value="{escape(value, quote=True)}"')
    if placeholder is not None:
        attributes.append(f'placeholder="{escape(placeholder, quote=True)}"')
    if autofocus:
        attributes.append("autofocus")

    shell = ui.element("span").classes("k-control-shell k-input-shell")
    if shell_classes:
        shell.classes(shell_classes)
    with shell:
        input_element = ui.element("input").classes("k-input")
        if classes:
            input_element.classes(classes)
        input_element.props(" ".join(attributes))
    return input_element


def select_input(
    *,
    name: str,
    aria_label: str,
    options: tuple[SelectOption, ...],
    value: str,
) -> Element:
    """Render a styled native select inside the shared focus-border shell."""

    with ui.element("label").classes("k-control-shell k-select-wrap"):
        ui.label(aria_label).classes("k-sr-only")
        with (
            ui.element("select")
            .classes("k-select")
            .props(
                f'name="{escape(name, quote=True)}" '
                f'aria-label="{escape(aria_label, quote=True)}"'
            )
        ) as select_element:
            for option in options:
                selected = " selected" if option.value == value else ""
                with ui.element("option").props(
                    f'value="{escape(option.value, quote=True)}"{selected}'
                ):
                    ui.label(option.label)
    return select_element


def checkbox_input(
    *,
    name: str,
    label: str,
    value: str,
    checked: bool,
) -> Element:
    """Render a labelled native checkbox using the shared control shell."""

    with ui.element("label").classes("k-control-shell k-check"):
        checkbox = ui.element("input").props(
            f'name="{escape(name, quote=True)}" type="checkbox" '
            f'value="{escape(value, quote=True)}"'
        )
        if checked:
            checkbox.props("checked")
        ui.label(label)
    return checkbox

"""Native Kanvas form controls with one consistent accessible structure."""

from __future__ import annotations

from dataclasses import dataclass

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

    attributes = [f"name={name!r}", f"type={input_type!r}", f"aria-label={aria_label!r}"]
    if value is not None:
        attributes.append(f"value={value!r}")
    if placeholder is not None:
        attributes.append(f"placeholder={placeholder!r}")
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
                f"name={name!r} aria-label={aria_label!r}"
            )
        ) as select_element:
            for option in options:
                selected = " selected" if option.value == value else ""
                with ui.element("option").props(f"value={option.value!r}{selected}"):
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
        checkbox = ui.element("input").props(f"name={name!r} type='checkbox' value={value!r}")
        if checked:
            checkbox.props("checked")
        ui.label(label)
    return checkbox


def textarea_input(
    *,
    name: str,
    aria_label: str,
    value: str | None = None,
    placeholder: str | None = None,
) -> Element:
    """Render a native textarea through the same labelled control boundary."""

    with ui.element("label").classes("k-control-shell k-textarea-shell"):
        ui.label(aria_label).classes("k-sr-only")
        textarea = ui.element("textarea").classes("k-textarea").props(
            f"name={name!r} aria-label={aria_label!r}"
        )
        if value is not None:
            textarea.props(f"value={value!r}")
        if placeholder is not None:
            textarea.props(f"placeholder={placeholder!r}")
    return textarea


def hidden_input(*, name: str, value: str) -> Element:
    """Render an escaped hidden form value for a native submission."""

    return ui.element("input").props(f"type='hidden' name={name!r} value={value!r}")

"""OLED-black profile selection screen."""

from __future__ import annotations

from nicegui import ui

from kasana.kanvas.components.controls import ButtonType, action_button
from kasana.kanvas.components.inputs import text_input
from kasana.kanvas.components.typography import page_title
from kasana.kanvas.profiles import profile_display_name
from kasana.kanvas.settings import Kanvas_Settings
from kasana.katalog.public import UserSummary


def render_profile_selection(
    settings: Kanvas_Settings, users: tuple[UserSummary, ...], *, error: str | None = None
) -> None:
    """Render profile selection, or the one-time owner bootstrap form."""

    with ui.element("main").classes("k-profile-screen").props('aria-label="Profiles"'):
        page_title("Who's watching?")
        if error is not None:
            ui.label(error).classes("k-action-status").props('aria-live="polite"')
        if not users:
            _bootstrap_form()
            return
        with ui.element("div").classes("k-profile-list"):
            for user in users:
                _profile_form(user)


def _profile_form(user: UserSummary) -> None:
    with (
        ui.element("form")
        .classes("k-profile-card")
        .props('method="post" action="/profiles/select"')
    ):
        ui.element("input").props(f'type="hidden" name="user_id" value="{user.id}"')
        ui.label(profile_display_name(user)).classes("k-profile-card__name")
        if user.is_disabled:
            ui.label("Disabled").classes("k-profile-card__state")
            return
        if user.pin_required:
            text_input(
                name="pin",
                input_type="text",
                placeholder="PIN",
                aria_label=f"PIN for {profile_display_name(user)}",
            ).props("minlength='2' maxlength='16' autocomplete='off' inputmode='numeric'")
        action_button("Select", button_type=ButtonType.SUBMIT, primary=True)


def _bootstrap_form() -> None:
    with (
        ui.element("form")
        .classes("k-profile-bootstrap")
        .props('method="post" action="/profiles/bootstrap"')
    ):
        ui.label("Create the first profile. It will be the owner.").classes(
            "k-profile-bootstrap__hint"
        )
        text_input(
            name="username",
            input_type="text",
            placeholder="Profile name",
            aria_label="Profile name",
        )
        text_input(
            name="display_name",
            input_type="text",
            placeholder="Display name (optional)",
            aria_label="Display name",
        )
        text_input(
            name="pin",
            input_type="text",
            placeholder="PIN (optional)",
            aria_label="Optional PIN",
        ).props("minlength='2' maxlength='16' autocomplete='off' inputmode='numeric'")
        action_button("Create owner profile", button_type=ButtonType.SUBMIT, primary=True)

"""Kanvas profile and Katalog user-profile contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from nicegui.client import Client
from nicegui.element import Element
from nicegui.page import page
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request

from kasana.kanvas import dashboard
from kasana.kanvas.profiles import ProfileSessions, SessionProfile
from kasana.kanvas.routes.profiles import render_profile_selection
from kasana.kanvas.settings import Kanvas_Settings
from kasana.katalog.api.contracts import UserAuthentication, UserRole, UserSummary, UserUpdate
from kasana.shared.profile_rules import PROFILE_ACCENT_COLOUR_DEFAULT


async def _asgi_app(_scope: Any, _receive: Any, _send: Any) -> None:
    return None


class _ProfileClient:
    def __init__(self, users: tuple[UserSummary, ...]) -> None:
        self.users = users
        self.authentication_requests: list[tuple[int, UserAuthentication]] = []

    async def __aenter__(self) -> _ProfileClient:
        return self

    async def __aexit__(self, *_arguments: object) -> None:
        return None

    async def list_users(self) -> tuple[UserSummary, ...]:
        return self.users

    async def authenticate_user(self, user_id: int, request: UserAuthentication) -> UserSummary:
        self.authentication_requests.append((user_id, request))
        return next(user for user in self.users if user.id == user_id)


def _request(session: dict[str, object]) -> Request:
    return Request({"type": "http", "headers": [], "session": session})


def _profile(
    user_id: int, *, disabled: bool = False, role: UserRole = UserRole.USER
) -> UserSummary:
    return UserSummary(
        id=user_id,
        username=f"profile-{user_id}",
        role=role,
        is_disabled=disabled,
    )


def test_profile_roles_are_explicit_finite_values() -> None:
    assert {role.value for role in UserRole} == {"owner", "admin", "user"}


def test_session_cookie_configuration_is_signed_http_only_and_same_site() -> None:
    settings = Kanvas_Settings()
    middleware = SessionMiddleware(
        app=_asgi_app,
        secret_key=settings.session_secret,
        session_cookie="kanvas_session",
        same_site="lax",
        https_only=settings.session_cookie_secure,
    )

    assert middleware.session_cookie == "kanvas_session"
    assert middleware.security_flags.startswith("httponly; samesite=lax")


async def test_selected_profile_persists_and_switches_without_sharing_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = _profile(3), _profile(8)
    client = _ProfileClient((first, second))
    sessions = ProfileSessions(Kanvas_Settings())
    monkeypatch.setattr(sessions, "_client", lambda: client)
    browser_session: dict[str, object] = {}
    request = _request(browser_session)

    assert await sessions.current(request) is None
    assert (await sessions.start(request, user_id=first.id, pin=None)).user.id == first.id
    persisted_first = await sessions.current(_request(browser_session))
    assert persisted_first is not None
    assert persisted_first.user.id == first.id
    assert (await sessions.start(request, user_id=second.id, pin="2468")).user.id == second.id
    persisted_second = await sessions.current(_request(browser_session))
    assert persisted_second is not None
    assert persisted_second.user.id == second.id
    assert client.authentication_requests == [
        (first.id, UserAuthentication(pin=None)),
        (second.id, UserAuthentication(pin="2468")),
    ]


async def test_disabled_profile_clears_a_previous_browser_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = ProfileSessions(Kanvas_Settings())
    monkeypatch.setattr(sessions, "_client", lambda: _ProfileClient((_profile(9, disabled=True),)))
    browser_session: dict[str, object] = {"kanvas_profile_id": 9}

    assert await sessions.current(_request(browser_session)) is None
    assert browser_session == {}


def test_administrator_role_check_does_not_grant_users_administration() -> None:
    assert not SessionProfile(_profile(1)).is_administrator
    assert SessionProfile(_profile(2, role=UserRole.ADMIN)).is_administrator
    assert SessionProfile(_profile(3, role=UserRole.OWNER)).is_administrator


def test_kanvas_settings_has_no_global_active_user_id() -> None:
    assert "user_id" not in Kanvas_Settings.model_fields


def test_profile_selection_renders_bootstrap_and_pin_controls() -> None:
    with Client(page("")) as bootstrap_client:
        render_profile_selection(Kanvas_Settings(), ())
        assert any(element.tag == "form" for element in bootstrap_client.elements.values())

    with Client(page("")) as selection_client:
        render_profile_selection(
            Kanvas_Settings(),
            (_profile(1), UserSummary(id=2, username="pinned", pin_required=True)),
            error="Try again.",
        )
        pin_input = next(
            element
            for element in selection_client.elements.values()
            if element.tag == "input" and _element_props(element).get("name") == "pin"
        )
        assert sum(element.tag == "input" for element in selection_client.elements.values()) >= 2
        assert _element_props(pin_input)["type"] == "text"
        assert _element_props(pin_input)["minlength"] == "2"
        assert _element_props(pin_input)["maxlength"] == "16"


async def test_profile_dashboard_session_and_administration_actions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    owner = SessionProfile(_profile(1, role=UserRole.OWNER))

    class Sessions:
        async def start(self, request: Request, *, user_id: int, pin: str | None) -> SessionProfile:
            request.scope["started"] = (user_id, pin)
            return owner

        async def bootstrap(self, request: Request, **_arguments: object) -> SessionProfile:
            request.scope["bootstrapped"] = True
            return owner

        def clear(self, request: Request) -> None:
            request.scope["cleared"] = True

    update_requests: list[tuple[int, UserUpdate]] = []

    class FakeKatalogClient:
        def __init__(self) -> None:
            pass

        async def __aenter__(self) -> FakeKatalogClient:
            return self

        async def __aexit__(self, *_arguments: object) -> None:
            return None

        async def create_user(self, _request: object) -> UserSummary:
            return _profile(2)

        async def update_user(self, user_id: int, request: UserUpdate) -> UserSummary:
            update_requests.append((user_id, request))
            return UserSummary(
                id=user_id,
                username=f"profile-{user_id}",
                role=UserRole.ADMIN,
                accent_colour=request.accent_colour or PROFILE_ACCENT_COLOUR_DEFAULT,
            )

        async def disable_user(self, _user_id: int) -> UserSummary:
            return _profile(2, disabled=True)

    class FormRequest:
        def __init__(self, values: dict[str, str]) -> None:
            self._values = values
            self.scope: dict[str, object] = {}

        async def form(self) -> dict[str, str]:
            return self._values

    class JsonRequest:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        async def json(self) -> object:
            return self._payload

    def profile_sessions(_settings: Kanvas_Settings) -> Sessions:
        return Sessions()

    def katalog_client(_url: str, *, timeout_seconds: float) -> FakeKatalogClient:
        del timeout_seconds
        return FakeKatalogClient()

    monkeypatch.setattr(dashboard, "ProfileSessions", profile_sessions)
    monkeypatch.setattr(dashboard, "KatalogClient", katalog_client)
    monkeypatch.setattr(dashboard, "_settings", Kanvas_Settings())
    monkeypatch.setenv("KASANA_CONFIG_DIRECTORY", str(tmp_path / "configs"))

    selected = FormRequest({"user_id": "2", "pin": "2468"})
    assert (await dashboard.select_profile(cast(Request, selected))).status_code == 303
    assert selected.scope["started"] == (2, "2468")
    bootstrapped = FormRequest({"username": "owner", "display_name": "Owner", "pin": ""})
    assert (await dashboard.bootstrap_profile(cast(Request, bootstrapped))).status_code == 303
    signed_out = _request({})
    assert (await dashboard.sign_out_profile(signed_out)).status_code == 303
    assert signed_out.scope["cleared"] is True

    async def current_profile(_request: Request) -> SessionProfile:
        return SessionProfile(
            UserSummary(
                id=owner.user.id,
                username=owner.user.username,
                role=owner.user.role,
                accent_colour="#224466",
            )
        )

    monkeypatch.setattr(dashboard, "_data_profile", current_profile)
    assert (
        await dashboard.create_profile_user(cast(Request, JsonRequest({"username": "two"})))
    ).status_code == 201
    assert (
        await dashboard.update_profile_user(
            2, cast(Request, JsonRequest({"displayName": "Two", "role": "admin"}))
        )
    ).status_code == 200
    assert (
        await dashboard.update_current_profile(
            cast(
                Request,
                JsonRequest(
                    {
                        "displayName": "Owner",
                        "pin": "1357",
                        "accent_colour": "#336699",
                    }
                ),
            )
        )
    ).status_code == 200
    current_profile_update = update_requests[-1][1]
    assert update_requests[-1][0] == 1
    assert current_profile_update.display_name == "Owner"
    assert current_profile_update.pin == "1357"
    assert current_profile_update.accent_colour == "#336699"
    preference_response = await dashboard.update_kanvas_preferences(
        cast(Request, JsonRequest({"accent_colour": "#336699"}))
    )
    assert preference_response.status_code == 200
    assert json.loads(bytes(preference_response.body))["accentColour"] == "#336699"
    assert not (tmp_path / "configs" / "config.kanvas.json").exists()
    assert (await dashboard.kanvas_theme_stylesheet(_request({}))).body == (
        b":root{--k-accent:#224466;}\n"
    )
    assert (
        await dashboard.disable_profile_user(2, cast(Request, JsonRequest({})))
    ).status_code == 200


def _element_props(element: Element) -> dict[str, object]:
    """Expose NiceGUI's internal test-only rendered attributes."""

    return cast(dict[str, object], element._props)  # pyright: ignore[reportPrivateUsage]

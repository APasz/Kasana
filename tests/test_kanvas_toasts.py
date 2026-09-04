"""Contracts for Kanvas's short-lived action feedback."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import cast

import pytest
from _pytest.monkeypatch import MonkeyPatch
from nicegui.client import Client
from nicegui.page import page
from pydantic import ValidationError
from starlette.datastructures import FormData
from starlette.requests import Request

from kasana.kanvas import dashboard
from kasana.kanvas.components.browser import BrowserComponent
from kasana.kanvas.components.controls import action_form_props
from kasana.kanvas.components.shell import page_shell
from kasana.kanvas.notifications import consume_toasts, queue_toast
from kasana.kanvas.profiles import SessionProfile
from kasana.kanvas.routes import api_administration, api_collections
from kasana.kanvas.routes import common as route_common
from kasana.kanvas.settings import Kanvas_Settings
from kasana.kanvas.viewmodels.toasts import ToastSeverity, ToastView
from kasana.katalog.public import UserRole, UserSummary


def _profile() -> SessionProfile:
    return SessionProfile(UserSummary(id=1, username="tester", role=UserRole.OWNER))


def _toast(title: str) -> ToastView:
    return ToastView(severity=ToastSeverity.SUCCESS, title=title)


def test_toasts_are_bounded_and_consumed_once() -> None:
    request = cast(Request, SimpleNamespace(session={}))

    for index in range(5):
        queue_toast(request, _toast(f"Saved {index}"))

    assert [toast.title for toast in consume_toasts(request)] == [
        "Saved 1",
        "Saved 2",
        "Saved 3",
        "Saved 4",
    ]
    assert consume_toasts(request) == ()


def test_toast_helpers_leave_non_session_test_requests_unchanged() -> None:
    request = Request({"type": "http", "query_string": b"", "headers": []})

    queue_toast(request, _toast("Saved"))

    assert consume_toasts(request) == ()


async def test_toast_feed_clears_the_session_batch(
    monkeypatch: MonkeyPatch,
) -> None:
    async def current_profile(_request: object) -> SessionProfile:
        return _profile()

    monkeypatch.setattr(api_administration, "data_profile", current_profile)
    request = Request({"type": "http", "query_string": b"", "headers": [], "session": {}})
    queue_toast(request, ToastView(severity=ToastSeverity.ERROR, title="Could not save changes"))

    first = await dashboard.consume_toasts_data(request)
    second = await dashboard.consume_toasts_data(request)

    assert json.loads(bytes(first.body)) == {
        "toasts": [{"severity": "error", "title": "Could not save changes", "detail": None}]
    }
    assert json.loads(bytes(second.body)) == {"toasts": []}


async def test_toast_feed_requires_a_profile(monkeypatch: MonkeyPatch) -> None:
    async def no_profile(_request: object) -> None:
        return None

    monkeypatch.setattr(api_administration, "data_profile", no_profile)
    request = Request({"type": "http", "query_string": b"", "headers": [], "session": {}})

    response = await dashboard.consume_toasts_data(request)

    assert response.status_code == 401


def test_toast_redirect_queues_a_valid_success_message() -> None:
    request = cast(Request, SimpleNamespace(session={}))

    response = route_common.toast_redirect(request, "/collections/4", "Collection created")

    assert response.status_code == 303
    assert response.headers["location"] == "/collections/4"
    assert consume_toasts(request) == (_toast("Collection created"),)


async def test_native_collection_action_queues_its_redirect_toast(monkeypatch: MonkeyPatch) -> None:
    class Catalogue:
        def __init__(self, _settings: Kanvas_Settings, _user_id: int | None = None) -> None:
            pass

        async def create_collection(self, *, name: str, overview: str | None) -> int:
            assert (name, overview) == ("Stargate", None)
            return 4

    async def current_profile(_request: object) -> SessionProfile:
        return _profile()

    class FormRequest:
        session: dict[str, object]

        def __init__(self) -> None:
            self.session = {}

        async def form(self) -> FormData:
            return FormData({"name": "Stargate", "overview": ""})

    monkeypatch.setattr(route_common, "data_profile", current_profile)
    monkeypatch.setattr(api_collections, "KanvasKatalogService", Catalogue)
    request = FormRequest()

    response = await dashboard.create_collection_action(cast(Request, request))

    assert response.headers["location"] == "/collections/4"
    assert consume_toasts(cast(Request, request)) == (_toast("Collection created"),)


def test_page_shell_mounts_toasts_for_signed_in_profiles() -> None:
    with Client(page("")) as client:
        with page_shell(Kanvas_Settings(), "/", "Home", _profile()):
            pass
        toasts = [
            element
            for element in client.elements.values()
            if element.tag == BrowserComponent.TOASTS
        ]

    assert len(toasts) == 1
    assert toasts[0]._props["source"] == "/kanvas/data/toasts/consume"  # pyright: ignore[reportPrivateUsage]


def test_toast_contract_and_native_form_hook_reject_ambiguous_values() -> None:
    with pytest.raises(ValidationError):
        ToastView(severity=ToastSeverity.INFO, title=" ")
    with pytest.raises(ValidationError):
        ToastView(severity=ToastSeverity.INFO, title="Saved", detail=" ")
    with pytest.raises(ValueError, match="internal absolute path"):
        action_form_props("//untrusted.example")
    with pytest.raises(ValueError, match="internal absolute path"):
        action_form_props("/\\untrusted.example")
    with pytest.raises(ValueError, match="internal absolute path"):
        action_form_props("/kanvas/actions\n/collections")

    assert action_form_props("/kanvas/actions/collections") == (
        'method="post" action="/kanvas/actions/collections" data-kanvas-action-form="true"'
    )

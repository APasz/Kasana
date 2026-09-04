"""One-time session storage for Kanvas action toasts."""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import cast

from starlette.requests import Request

from kasana.kanvas.viewmodels.toasts import ToastView

_TOAST_SESSION_KEY = "kanvas_toasts"
_MAX_PENDING_TOASTS = 4


def queue_toast(request: Request, toast: ToastView) -> None:
    """Queue a toast for the next signed-in page without growing the session unbounded."""

    session = _session(request)
    if session is None:
        return
    queued = [*(_validated_toasts(session.get(_TOAST_SESSION_KEY))), toast]
    session[_TOAST_SESSION_KEY] = [
        queued_toast.model_dump(mode="json") for queued_toast in queued[-_MAX_PENDING_TOASTS:]
    ]


def consume_toasts(request: Request) -> tuple[ToastView, ...]:
    """Return and remove the queued batch so reloads cannot repeat old outcomes."""

    session = _session(request)
    if session is None:
        return ()
    return _validated_toasts(session.pop(_TOAST_SESSION_KEY, ()))


def _session(request: Request) -> MutableMapping[str, object] | None:
    """Accept direct endpoint tests while using Starlette's session mapping in production."""

    try:
        session = getattr(request, "session", None)
    except AssertionError:
        return None
    if not isinstance(session, MutableMapping):
        return None
    return cast(MutableMapping[str, object], session)


def _validated_toasts(value: object) -> tuple[ToastView, ...]:
    if not isinstance(value, list):
        return ()
    toasts: list[ToastView] = []
    for raw_toast in cast(list[object], value[-_MAX_PENDING_TOASTS:]):
        try:
            toasts.append(ToastView.model_validate(raw_toast))
        except ValueError:
            continue
    return tuple(toasts)

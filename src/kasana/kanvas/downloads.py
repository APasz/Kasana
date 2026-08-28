"""Small request-bound safeguards for native Kanvas download forms."""

from __future__ import annotations

from hmac import compare_digest
from secrets import token_urlsafe

from starlette.requests import Request

_DOWNLOAD_CSRF_SESSION_KEY = "kanvas_download_csrf"
_DOWNLOAD_CSRF_TOKEN_MIN_LENGTH = 32


def issue_download_csrf_token(request: Request) -> str:
    """Return the per-session synchronizer token used by native download forms."""

    token = request.session.get(_DOWNLOAD_CSRF_SESSION_KEY)
    if isinstance(token, str) and len(token) >= _DOWNLOAD_CSRF_TOKEN_MIN_LENGTH:
        return token
    token = token_urlsafe(32)
    request.session[_DOWNLOAD_CSRF_SESSION_KEY] = token
    return token


def valid_download_csrf_token(request: Request, submitted_token: str | None) -> bool:
    """Confirm a form token without creating state during a POST request."""

    expected_token = request.session.get(_DOWNLOAD_CSRF_SESSION_KEY)
    return (
        isinstance(expected_token, str)
        and isinstance(submitted_token, str)
        and len(submitted_token) >= _DOWNLOAD_CSRF_TOKEN_MIN_LENGTH
        and compare_digest(submitted_token, expected_token)
    )

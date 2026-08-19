"""Browser-session profile selection for Kanvas."""

from __future__ import annotations

from dataclasses import dataclass
from secrets import token_urlsafe
from time import monotonic
from typing import TypeGuard

from starlette.requests import Request

from kasana.kanvas.settings import Kanvas_Settings
from kasana.katalog.public import (
    KatalogClient,
    KatalogClientError,
    UserAuthentication,
    UserCreate,
    UserRole,
    UserSummary,
)

_SESSION_USER_ID = "kanvas_profile_id"
_SESSION_ID = "kanvas_session_id"


class ProfileSessionRegistry:
    """Track live browser session IDs so a profile switch invalidates stale pages."""

    def __init__(self) -> None:
        self._active_until: dict[str, float] = {}
        self._revoked_until: dict[str, float] = {}

    def activate(self, session_id: str, *, max_age_seconds: int) -> None:
        now = monotonic()
        self._discard_expired(now)
        expires_at = now + max_age_seconds
        self._active_until[session_id] = expires_at
        self._revoked_until.pop(session_id, None)

    def revoke(self, session_id: str, *, max_age_seconds: int) -> None:
        now = monotonic()
        self._discard_expired(now)
        self._active_until.pop(session_id, None)
        self._revoked_until[session_id] = now + max_age_seconds

    def is_active(self, session_id: str) -> bool:
        now = monotonic()
        self._discard_expired(now)
        if session_id in self._revoked_until:
            return False
        expires_at = self._active_until.get(session_id)
        return expires_at is None or expires_at > now

    def _discard_expired(self, now: float) -> None:
        for session_ids in (self._active_until, self._revoked_until):
            expired = tuple(
                session_id for session_id, expires_at in session_ids.items() if expires_at <= now
            )
            for session_id in expired:
                del session_ids[session_id]


_PROFILE_SESSION_REGISTRY = ProfileSessionRegistry()


@dataclass(frozen=True)
class SessionProfile:
    """The live Katalog profile attached to one signed browser session."""

    user: UserSummary

    @property
    def is_administrator(self) -> bool:
        return self.user.role in {UserRole.OWNER, UserRole.ADMIN}


class ProfileSessions:
    """Resolve and establish sessions without keeping an identity in process settings."""

    def __init__(
        self, settings: Kanvas_Settings, *, registry: ProfileSessionRegistry | None = None
    ) -> None:
        self._settings = settings
        self._registry = registry or _PROFILE_SESSION_REGISTRY

    async def profiles(self) -> tuple[UserSummary, ...]:
        async with self._client() as client:
            return await client.list_users()

    async def current(self, request: Request) -> SessionProfile | None:
        raw_user_id = request.session.get(_SESSION_USER_ID)
        raw_session_id = request.session.get(_SESSION_ID)
        if (
            not isinstance(raw_user_id, int)
            or raw_user_id <= 0
            or not _valid_session_id(raw_session_id)
            or not self._registry.is_active(raw_session_id)
        ):
            self._clear(request)
            return None
        user = next((user for user in await self.profiles() if user.id == raw_user_id), None)
        if user is None or user.is_disabled:
            self._clear(request)
            return None
        return SessionProfile(user)

    async def start(self, request: Request, *, user_id: int, pin: str | None) -> SessionProfile:
        async with self._client() as client:
            user = await client.authenticate_user(user_id, UserAuthentication(pin=pin))
        self._clear(request)
        session_id = token_urlsafe(32)
        request.session[_SESSION_USER_ID] = user.id
        request.session[_SESSION_ID] = session_id
        self._registry.activate(session_id, max_age_seconds=self._settings.session_max_age_seconds)
        return SessionProfile(user)

    async def bootstrap(
        self,
        request: Request,
        *,
        username: str,
        display_name: str | None,
        pin: str | None,
    ) -> SessionProfile:
        """Create the sole initial owner before authorization exists."""

        if await self.profiles():
            raise ValueError("A profile already exists. Select it instead.")
        async with self._client() as client:
            user = await client.create_user(
                UserCreate(
                    username=username,
                    display_name=display_name,
                    role=UserRole.OWNER,
                    pin=pin,
                )
            )
        self._clear(request)
        session_id = token_urlsafe(32)
        request.session[_SESSION_USER_ID] = user.id
        request.session[_SESSION_ID] = session_id
        self._registry.activate(session_id, max_age_seconds=self._settings.session_max_age_seconds)
        return SessionProfile(user)

    def clear(self, request: Request) -> None:
        self._clear(request)

    async def current_for_page(
        self, request: Request, *, expected_user_id: int
    ) -> SessionProfile | None:
        """Resolve a live page callback only when it still belongs to its rendered profile."""

        profile = await self.current(request)
        if profile is None or profile.user.id != expected_user_id:
            return None
        return profile

    def _clear(self, request: Request) -> None:
        raw_session_id = request.session.get(_SESSION_ID)
        if _valid_session_id(raw_session_id):
            self._registry.revoke(
                raw_session_id, max_age_seconds=self._settings.session_max_age_seconds
            )
        request.session.clear()

    def _client(self) -> KatalogClient:
        return KatalogClient(
            str(self._settings.katalog_url), timeout_seconds=self._settings.katalog_timeout_seconds
        )


def profile_display_name(user: UserSummary) -> str:
    """Choose one short, non-empty label for a profile control."""

    return user.display_name or user.username


def is_profile_access_error(error: KatalogClientError) -> bool:
    """Identify a rejected PIN or disabled profile without exposing internals."""

    return error.kind.value in {"validation", "not_found"}


def _valid_session_id(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and 32 <= len(value) <= 128

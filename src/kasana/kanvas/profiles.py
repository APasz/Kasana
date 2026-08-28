"""Browser-session profile selection for Kanvas."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from secrets import token_urlsafe
from time import monotonic
from typing import TypeGuard

from starlette.requests import Request

from kasana.kanvas.katalog_clients import katalog_client_context
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
        self._profiles: dict[int, _CachedProfile] = {}

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

    def cached_profile(self, user_id: int) -> UserSummary | None:
        """Return one non-expired profile snapshot without a catalogue round trip."""

        now = monotonic()
        self._discard_expired(now)
        cached = self._profiles.get(user_id)
        return cached.user if cached is not None else None

    def cache_profile(self, user: UserSummary, *, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            raise ValueError("Profile cache TTL must be positive.")
        now = monotonic()
        self._discard_expired(now)
        self._profiles[user.id] = _CachedProfile(user=user, expires_at=now + ttl_seconds)

    def invalidate_profile(self, user_id: int) -> None:
        self._profiles.pop(user_id, None)

    def _discard_expired(self, now: float) -> None:
        for session_ids in (self._active_until, self._revoked_until):
            expired = tuple(
                session_id for session_id, expires_at in session_ids.items() if expires_at <= now
            )
            for session_id in expired:
                del session_ids[session_id]
        expired_profile_ids = tuple(
            user_id
            for user_id, profile in self._profiles.items()
            if profile.expires_at <= now
        )
        for user_id in expired_profile_ids:
            del self._profiles[user_id]


_PROFILE_SESSION_REGISTRY = ProfileSessionRegistry()


@dataclass(frozen=True)
class _CachedProfile:
    user: UserSummary
    expires_at: float


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
            users = await client.list_users()
        for user in users:
            self.remember(user)
        return users

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
        user = self._registry.cached_profile(raw_user_id)
        if user is None:
            async with self._client() as client:
                user = await client.get_session_profile(raw_user_id)
            self.remember(user)
        if user.is_disabled:
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
        self.remember(user)
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
        self.remember(user)
        return SessionProfile(user)

    def clear(self, request: Request) -> None:
        self._clear(request)

    def remember(self, user: UserSummary) -> None:
        """Refresh the process-local snapshot after a successful profile mutation."""

        self._registry.cache_profile(user, ttl_seconds=self._settings.profile_cache_ttl_seconds)

    def forget(self, user_id: int) -> None:
        self._registry.invalidate_profile(user_id)

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

    @asynccontextmanager
    async def _client(self) -> AsyncGenerator[KatalogClient]:
        async with katalog_client_context(
            self._settings, client_factory=KatalogClient
        ) as client:
            yield client


def profile_display_name(user: UserSummary) -> str:
    """Choose one short, non-empty label for a profile control."""

    return user.display_name or user.username


def is_profile_access_error(error: KatalogClientError) -> bool:
    """Identify a rejected PIN or disabled profile without exposing internals."""

    return error.kind.value in {"validation", "not_found"}


def _valid_session_id(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and 32 <= len(value) <= 128

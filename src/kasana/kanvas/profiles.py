"""Browser-session profile selection for Kanvas."""

from __future__ import annotations

from dataclasses import dataclass

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


@dataclass(frozen=True)
class SessionProfile:
    """The live Katalog profile attached to one signed browser session."""

    user: UserSummary

    @property
    def is_administrator(self) -> bool:
        return self.user.role in {UserRole.OWNER, UserRole.ADMIN}


class ProfileSessions:
    """Resolve and establish sessions without keeping an identity in process settings."""

    def __init__(self, settings: Kanvas_Settings) -> None:
        self._settings = settings

    async def profiles(self) -> tuple[UserSummary, ...]:
        async with self._client() as client:
            return await client.list_users()

    async def current(self, request: Request) -> SessionProfile | None:
        raw_user_id = request.session.get(_SESSION_USER_ID)
        if not isinstance(raw_user_id, int) or raw_user_id <= 0:
            return None
        user = next((user for user in await self.profiles() if user.id == raw_user_id), None)
        if user is None or user.is_disabled:
            request.session.clear()
            return None
        return SessionProfile(user)

    async def start(self, request: Request, *, user_id: int, pin: str | None) -> SessionProfile:
        async with self._client() as client:
            user = await client.authenticate_user(user_id, UserAuthentication(pin=pin))
        request.session.clear()
        request.session[_SESSION_USER_ID] = user.id
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
        request.session.clear()
        request.session[_SESSION_USER_ID] = user.id
        return SessionProfile(user)

    def clear(self, request: Request) -> None:
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

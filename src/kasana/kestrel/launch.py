"""Typed creation of Katalog playback plans for Kestrel commands."""

from __future__ import annotations

from typing import Protocol

from kasana.katalog.public import (
    PlaybackPlanContext,
    PlaybackPlanLaunch,
    PlaybackPlanRequest,
    UserSummary,
)
from kasana.kestrel.player import KestrelPlaybackError


class PlaybackPlanCatalogClient(Protocol):
    async def list_users(self) -> tuple[UserSummary, ...]: ...

    async def create_playback_plan(self, request: PlaybackPlanRequest) -> PlaybackPlanLaunch: ...


async def create_launch_token(
    catalog: PlaybackPlanCatalogClient, *, user: str, context: PlaybackPlanContext
) -> str:
    """Resolve a user reference and create one opaque Katalog launch token."""

    user_id = await resolve_user_id(catalog, user)
    try:
        request = PlaybackPlanRequest(user_id=user_id, context=context)
        launch = await catalog.create_playback_plan(request)
    except Exception as error:
        raise KestrelPlaybackError("Katalog could not create a playback plan.") from error
    return launch.launch_token


async def resolve_user_id(catalog: PlaybackPlanCatalogClient, user: str) -> int:
    """Resolve a positive numeric ID or exactly one configured username."""

    normalized = user.strip()
    if not normalized:
        raise KestrelPlaybackError("A playback user is required.")
    if normalized.isdecimal():
        user_id = int(normalized)
        if user_id > 0:
            return user_id
        raise KestrelPlaybackError("A playback user ID must be positive.")
    try:
        users = await catalog.list_users()
    except Exception as error:
        raise KestrelPlaybackError("Katalog users could not be resolved.") from error
    matching_users = tuple(candidate for candidate in users if candidate.username == normalized)
    if len(matching_users) != 1:
        raise KestrelPlaybackError("The requested playback user does not exist.")
    return matching_users[0].id

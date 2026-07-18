"""Tests for Kestrel's typed playback-plan convenience layer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kasana.katalog.public import (
    PlaybackPlanLaunch,
    PlaybackPlanRequest,
    StandalonePlaybackContext,
    UserSummary,
)
from kasana.kestrel.launch import create_launch_token, resolve_user_id
from kasana.kestrel.player import KestrelPlaybackError


class FakePlaybackPlanCatalog:
    def __init__(self) -> None:
        self.request: PlaybackPlanRequest | None = None

    async def list_users(self) -> tuple[UserSummary, ...]:
        return (UserSummary(id=3, username="owner", display_name="Owner"),)

    async def create_playback_plan(self, request: PlaybackPlanRequest) -> PlaybackPlanLaunch:
        self.request = request
        return PlaybackPlanLaunch(
            launch_token="l" * 43,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )


async def test_create_launch_token_resolves_usernames_and_keeps_context_typed() -> None:
    catalog = FakePlaybackPlanCatalog()
    context = StandalonePlaybackContext(item_id=42)

    token = await create_launch_token(catalog, user="owner", context=context)

    assert token == "l" * 43
    assert catalog.request == PlaybackPlanRequest(user_id=3, context=context)
    assert await resolve_user_id(catalog, "7") == 7


async def test_user_resolution_rejects_unknown_names() -> None:
    with pytest.raises(KestrelPlaybackError, match="does not exist"):
        await resolve_user_id(FakePlaybackPlanCatalog(), "missing")

"""Home page view models."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from kasana.kanvas.viewmodels.library import PosterView


class HomeRailKind(StrEnum):
    """The distinct jobs performed by the compact Home rails."""

    GENERIC = "generic"
    CONTINUE = "continue"
    ON_DECK = "on_deck"
    RECENTLY_ADDED = "recently_added"


class MediaRailView(BaseModel):
    """A compact titled rail of posters."""

    model_config = ConfigDict(frozen=True)

    kind: HomeRailKind = HomeRailKind.GENERIC
    title: str = Field(min_length=1, max_length=80)
    posters: tuple[PosterView, ...]

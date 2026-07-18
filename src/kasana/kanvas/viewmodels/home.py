"""Home page view models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from kasana.kanvas.viewmodels.library import PosterView


class MediaRailView(BaseModel):
    """A compact titled rail of posters."""

    model_config = ConfigDict(frozen=True)

    title: str = Field(min_length=1, max_length=80)
    posters: tuple[PosterView, ...]

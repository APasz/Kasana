"""Item-detail presentation models with no playable media locations."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from kasana.kanvas.viewmodels.library import PosterView


class ItemDetailView(BaseModel):
    """Safe detail data for the first Kanvas item page."""

    model_config = ConfigDict(frozen=True)

    id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=1_000)
    kind: str = Field(min_length=1, max_length=32)
    year: int | None = Field(default=None, ge=1, le=9999)
    overview: str | None = Field(default=None, max_length=20_000)
    poster_url: str | None = Field(default=None, alias="posterUrl")
    backdrop_url: str | None = Field(default=None, alias="backdropUrl")
    runtime_label: str | None = Field(default=None, max_length=100, alias="runtimeLabel")
    progress_percent: int | None = Field(default=None, ge=0, le=100, alias="progressPercent")
    watched: bool = False
    available: bool
    children: tuple[PosterView, ...] = ()

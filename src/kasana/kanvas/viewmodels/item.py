"""Item-detail presentation models with no playable media locations."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from kasana.kanvas.viewmodels.library import ArtworkShape, PlaceholderArtView, PosterView


class IncludedCollectionView(BaseModel):
    """One direct collection placement rendered on an item page."""

    model_config = ConfigDict(frozen=True)

    id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=1_000)
    revision: int = Field(ge=1)
    relationship: str | None = Field(default=None, max_length=32)


class CollectionChoiceView(BaseModel):
    """One writable collection target for an administrator item-page control."""

    model_config = ConfigDict(frozen=True)

    id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=1_000)
    revision: int = Field(ge=1)


class DownloadOptionView(BaseModel):
    """One Katalog-confirmed media version available for a native download form."""

    model_config = ConfigDict(frozen=True)

    media_file_id: int = Field(gt=0, alias="mediaFileId")
    label: str = Field(min_length=1, max_length=200)


class ItemDetailView(BaseModel):
    """Safe detail data for the first Kanvas item page."""

    model_config = ConfigDict(frozen=True)

    id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=1_000)
    kind: str = Field(min_length=1, max_length=32)
    year: int | None = Field(default=None, ge=1, le=9999)
    overview: str | None = Field(default=None, max_length=20_000)
    poster_url: str | None = Field(default=None, alias="posterUrl")
    artwork_shape: ArtworkShape = Field(default=ArtworkShape.PORTRAIT, alias="artworkShape")
    poster_placeholder: PlaceholderArtView = Field(alias="posterPlaceholder")
    backdrop_url: str | None = Field(default=None, alias="backdropUrl")
    runtime_label: str | None = Field(default=None, max_length=100, alias="runtimeLabel")
    progress_percent: int | None = Field(default=None, ge=0, le=100, alias="progressPercent")
    watched: bool = False
    available: bool
    download_options: tuple[DownloadOptionView, ...] = Field(default=(), alias="downloadOptions")
    child_section_title: Literal["Episodes", "Seasons"] = Field(
        default="Episodes", alias="childSectionTitle"
    )
    children: tuple[PosterView, ...] = ()
    included_collections: tuple[IncludedCollectionView, ...] = Field(
        default=(), alias="includedCollections"
    )
    available_collections: tuple[CollectionChoiceView, ...] = Field(
        default=(), alias="availableCollections"
    )

    @property
    def downloadable(self) -> bool:
        """Expose download eligibility from the authoritative version list alone."""

        return bool(self.download_options)

"""Capability-specific protocols for metadata providers."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from kasana.shared.metadata import (
    ArtworkContent,
    ArtworkDownload,
    ArtworkReference,
    EpisodeDetails,
    MovieDetails,
    ProviderCapability,
    ProviderReference,
    SearchQuery,
    SearchResult,
    SeasonDetails,
    SeriesDetails,
)


class MetadataProvider(Protocol):
    @property
    def provider_name(self) -> str: ...

    @property
    def capabilities(self) -> frozenset[ProviderCapability]: ...

    def supports(self, capability: ProviderCapability) -> bool: ...


class MovieSearchProvider(MetadataProvider, Protocol):
    async def search_movies(self, query: SearchQuery) -> tuple[SearchResult, ...]: ...


class SeriesSearchProvider(MetadataProvider, Protocol):
    async def search_series(self, query: SearchQuery) -> tuple[SearchResult, ...]: ...


class MovieDetailsProvider(MetadataProvider, Protocol):
    async def get_movie(self, reference: ProviderReference) -> MovieDetails: ...


class SeriesDetailsProvider(MetadataProvider, Protocol):
    async def get_series(self, reference: ProviderReference) -> SeriesDetails: ...


class SeasonDetailsProvider(MetadataProvider, Protocol):
    async def get_season(
        self, series_reference: ProviderReference, season_number: int
    ) -> SeasonDetails: ...


class EpisodeDetailsProvider(MetadataProvider, Protocol):
    async def get_episode(
        self,
        series_reference: ProviderReference,
        season_number: int,
        episode_number: int,
    ) -> EpisodeDetails: ...


class ArtworkProvider(MetadataProvider, Protocol):
    async def get_artwork(self, reference: ArtworkReference) -> ArtworkContent: ...


class StreamingArtworkProvider(ArtworkProvider, Protocol):
    async def download_artwork(
        self,
        reference: ArtworkReference,
        destination: Path,
        *,
        maximum_size_bytes: int,
    ) -> ArtworkDownload: ...

"""Provider detail retrieval and metadata refresh operations."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from kasana.katalog.metadata.candidates import MetadataWorkflowError
from kasana.katalog.metadata.review import ProviderDetails
from kasana.katalog.metadata.scoring import SupportedItemKind
from kasana.katalog.models import ZaisanKind
from kasana.shared.metadata import (
    ArtworkContent,
    ArtworkDownload,
    ArtworkReference,
    MovieDetails,
    ProviderCapability,
    ProviderReference,
    SearchQuery,
    SearchResult,
    SeriesDetails,
)


class MetadataProvider(Protocol):
    """The provider operations Katalog coordinates without owning implementation."""

    @property
    def provider_name(self) -> str: ...

    @property
    def capabilities(self) -> frozenset[ProviderCapability]: ...

    def supports(self, capability: ProviderCapability) -> bool: ...

    async def search_movies(self, query: SearchQuery) -> tuple[SearchResult, ...]: ...

    async def search_series(self, query: SearchQuery) -> tuple[SearchResult, ...]: ...

    async def get_movie(self, reference: ProviderReference, /) -> MovieDetails: ...

    async def get_series(self, reference: ProviderReference, /) -> SeriesDetails: ...

    async def get_artwork(self, reference: ArtworkReference, /) -> ArtworkContent: ...


class ArtworkStreamingProvider(MetadataProvider, Protocol):
    async def download_artwork(
        self,
        reference: ArtworkReference,
        destination: Path,
        *,
        maximum_size_bytes: int,
    ) -> ArtworkDownload: ...


async def fetch_details(
    provider: MetadataProvider, item_kind: SupportedItemKind, reference: ProviderReference
) -> ProviderDetails:
    """Retrieve the full record after validating the provider capability."""

    if item_kind is ZaisanKind.MOVIE:
        if not provider.supports(ProviderCapability.GET_MOVIE):
            msg = f"Provider {provider.provider_name!r} cannot retrieve movie details."
            raise MetadataWorkflowError(msg)
        return await provider.get_movie(reference)
    if not provider.supports(ProviderCapability.GET_SERIES):
        msg = f"Provider {provider.provider_name!r} cannot retrieve series details."
        raise MetadataWorkflowError(msg)
    return await provider.get_series(reference)


def details_to_search_result(details: ProviderDetails) -> SearchResult:
    return SearchResult(
        reference=details.reference,
        media_kind=details.media_kind,
        title=details.title,
        original_title=details.original_title,
        translated_title=details.translated_title,
        overview=details.overview,
        release_date=details.release_date,
        poster=details.poster,
        backdrop=details.backdrop,
        original_language=details.original_language,
    )


def provider_for(name: str, providers: tuple[MetadataProvider, ...]) -> MetadataProvider:
    for provider in providers:
        if provider.provider_name == name:
            return provider
    msg = f"No configured metadata provider is named {name!r}."
    raise MetadataWorkflowError(msg)

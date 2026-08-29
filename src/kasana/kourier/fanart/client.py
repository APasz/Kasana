"""Bounded aiohttp Fanart.tv client for supplemental movie poster variants."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import aiohttp
from pydantic import BaseModel, ValidationError
from yarl import URL

from kasana.kourier.errors import KourierError
from kasana.kourier.fanart.constants import FANART_PROVIDER
from kasana.kourier.fanart.mapping import poster_artwork
from kasana.kourier.fanart.payloads import FanartMoviePayload
from kasana.kourier.http import (
    KASANA_USER_AGENT,
    AsyncSleeper,
    BoundedHttpProvider,
    Clock,
    request_error,
)
from kasana.kourier.settings import FanartSettings
from kasana.shared.metadata import (
    ArtworkContent,
    ArtworkDownload,
    ArtworkReference,
    MovieDetails,
    PosterListing,
    PosterLookup,
    ProviderCapability,
    ProviderErrorCategory,
    ProviderMediaKind,
    ProviderReference,
    SearchQuery,
    SearchResult,
    SeriesDetails,
)


@dataclass(frozen=True)
class _MovieLookup:
    identifier: str
    provider_id: str


_FANART_CAPABILITIES: Final[frozenset[ProviderCapability]] = frozenset(
    {
        ProviderCapability.GET_ARTWORK,
        ProviderCapability.LIST_POSTERS_BY_EXTERNAL_ID,
    }
)


class FanartProvider(BoundedHttpProvider):
    """Retrieves Fanart.tv movie posters without participating in metadata matching."""

    def __init__(
        self,
        settings: FanartSettings,
        *,
        session: aiohttp.ClientSession | None = None,
        sleeper: AsyncSleeper = asyncio.sleep,
        clock: Clock | None = None,
    ) -> None:
        if not settings.is_configured:
            msg = "Fanart.tv requires an API key or personal client key."
            raise ValueError(msg)
        super().__init__(
            settings,
            provider_name=FANART_PROVIDER,
            display_name="Fanart.tv",
            session=session,
            sleeper=sleeper,
            clock=clock,
        )
        self.settings = settings

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return _FANART_CAPABILITIES

    async def __aenter__(self) -> FanartProvider:
        await self._get_session()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def search_movies(self, query: SearchQuery) -> tuple[SearchResult, ...]:
        del query
        raise self._unsupported_metadata_operation()

    async def search_series(self, query: SearchQuery) -> tuple[SearchResult, ...]:
        del query
        raise self._unsupported_metadata_operation()

    async def get_movie(self, item_reference: ProviderReference) -> MovieDetails:
        del item_reference
        raise self._unsupported_metadata_operation()

    async def get_series(self, item_reference: ProviderReference) -> SeriesDetails:
        del item_reference
        raise self._unsupported_metadata_operation()

    async def list_posters_by_external_id(self, lookup: PosterLookup) -> PosterListing | None:
        """Return Fanart.tv movie posters for a matched title when it has a usable ID."""

        movie = self._movie_lookup(lookup)
        if movie is None:
            return None
        try:
            payload = await self._request_json(("movies", movie.identifier))
        except KourierError as error:
            if error.category is ProviderErrorCategory.NOT_FOUND:
                return PosterListing(
                    provider=self.provider_name, provider_id=movie.provider_id, posters=()
                )
            raise
        response: FanartMoviePayload = self._parse_payload(FanartMoviePayload, payload)
        preferred_language = self.settings.language.partition("-")[0].lower()
        posters = tuple(poster_artwork(image) for image in response.movieposter)
        return PosterListing(
            provider=self.provider_name,
            provider_id=movie.provider_id,
            posters=tuple(
                sorted(
                    posters,
                    key=lambda artwork: (
                        0 if artwork.language == preferred_language else 1,
                        -(artwork.vote_count or 0),
                        -((artwork.width or 0) * (artwork.height or 0)),
                        artwork.raw_path,
                    ),
                )
            ),
        )

    async def get_artwork(self, item_reference: ArtworkReference) -> ArtworkContent:
        self._validate_artwork_reference(item_reference)
        content, media_type = await self._fetch_artwork(URL(str(item_reference.source_url)))
        return ArtworkContent(reference=item_reference, content=content, media_type=media_type)

    async def download_artwork(
        self,
        item_reference: ArtworkReference,
        destination: Path,
        *,
        maximum_size_bytes: int,
    ) -> ArtworkDownload:
        """Stream Fanart.tv artwork into Katalog's temporary file."""

        self._validate_artwork_reference(item_reference)
        return await self._download_artwork(
            URL(str(item_reference.source_url)), destination, maximum_size_bytes
        )

    async def _request_json(self, path_parts: tuple[str, ...]) -> Mapping[str, object]:
        return await self._fetch_json(self._endpoint_url(path_parts), self._api_headers())

    def _movie_lookup(self, lookup: PosterLookup) -> _MovieLookup | None:
        if lookup.media_kind is not ProviderMediaKind.MOVIE:
            return None
        tmdb_id = self._external_identifier(lookup, "tmdb")
        if tmdb_id is not None:
            if not tmdb_id.isdecimal():
                raise request_error(self.provider_name, "TMDB identifiers must be decimal numbers.")
            return _MovieLookup(identifier=tmdb_id, provider_id=f"tmdb:{tmdb_id}")
        imdb_id = self._external_identifier(lookup, "imdb")
        if imdb_id is None:
            return None
        if not imdb_id.startswith("tt") or not imdb_id[2:].isdecimal():
            raise request_error(
                self.provider_name, "IMDb identifiers must begin with 'tt' and digits."
            )
        return _MovieLookup(identifier=imdb_id, provider_id=f"imdb:{imdb_id}")

    @staticmethod
    def _external_identifier(lookup: PosterLookup, namespace: str) -> str | None:
        if lookup.reference.provider == namespace:
            return lookup.reference.raw_id
        for identifier in lookup.external_ids:
            if identifier.namespace == namespace:
                return identifier.value
        return None

    def _api_headers(self) -> dict[str, str]:
        headers = {"User-Agent": KASANA_USER_AGENT, "Accept": "application/json"}
        if self.settings.api_key is not None:
            headers["api-key"] = self.settings.api_key.get_secret_value()
        if self.settings.client_key is not None:
            headers["client-key"] = self.settings.client_key.get_secret_value()
        return headers

    def _endpoint_url(self, path_parts: tuple[str, ...]) -> URL:
        url = URL(str(self.settings.base_url).rstrip("/"))
        for part in path_parts:
            url = url / part
        return url

    def _validate_artwork_reference(self, item_reference: ArtworkReference) -> None:
        if item_reference.provider != self.provider_name or item_reference.source_url is None:
            raise KourierError(
                ProviderErrorCategory.UNSUPPORTED_OPERATION,
                "Fanart.tv artwork requires a Fanart.tv reference with a source URL.",
                provider=self.provider_name,
            )

    def _parse_payload[Model: BaseModel](
        self, model: type[Model], payload: Mapping[str, object]
    ) -> Model:
        try:
            return model.model_validate(payload)
        except ValidationError as error:
            raise KourierError(
                ProviderErrorCategory.MALFORMED_RESPONSE,
                "Fanart.tv returned an unexpected response payload.",
                provider=self.provider_name,
            ) from error

    def _unsupported_metadata_operation(self) -> KourierError:
        return KourierError(
            ProviderErrorCategory.UNSUPPORTED_OPERATION,
            "Fanart.tv supplies supplemental artwork only, not metadata matching.",
            provider=self.provider_name,
        )

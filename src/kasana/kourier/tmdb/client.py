"""Bounded aiohttp TMDB client implementing Kourier provider contracts."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Final

import aiohttp
from pydantic import BaseModel, ValidationError
from yarl import URL

from kasana.kourier.errors import KourierError
from kasana.kourier.http import (
    KASANA_USER_AGENT,
    AsyncSleeper,
    BoundedHttpProvider,
    Clock,
    request_error,
)
from kasana.kourier.settings import TMDBSettings
from kasana.kourier.tmdb.constants import TMDB_PROVIDER
from kasana.kourier.tmdb.mapping import (
    artwork,
    countries,
    episode_details,
    external_ids,
    genres,
    movie_search_result,
    poster_artwork,
    reference,
    series_search_result,
)
from kasana.kourier.tmdb.payloads import (
    TMDBEpisodePayload,
    TMDBImagesPayload,
    TMDBMoviePayload,
    TMDBMovieSearchPage,
    TMDBSeasonPayload,
    TMDBSeriesPayload,
    TMDBSeriesSearchPage,
)
from kasana.shared.metadata import (
    ArtworkContent,
    ArtworkDownload,
    ArtworkKind,
    ArtworkReference,
    EpisodeDetails,
    MovieDetails,
    ProviderCapability,
    ProviderErrorCategory,
    ProviderMediaKind,
    ProviderReference,
    SearchQuery,
    SearchResult,
    SeasonDetails,
    SeriesDetails,
)

_TMDB_CAPABILITIES: Final[frozenset[ProviderCapability]] = frozenset(
    {
        ProviderCapability.SEARCH_MOVIES,
        ProviderCapability.SEARCH_SERIES,
        ProviderCapability.GET_MOVIE,
        ProviderCapability.GET_SERIES,
        ProviderCapability.GET_SEASON,
        ProviderCapability.GET_EPISODE,
        ProviderCapability.GET_ARTWORK,
        ProviderCapability.LIST_POSTERS,
    }
)


class TMDBProvider(BoundedHttpProvider):
    """Maps TMDB HTTP responses to Kourier's provider-neutral contracts."""

    def __init__(
        self,
        settings: TMDBSettings,
        *,
        session: aiohttp.ClientSession | None = None,
        sleeper: AsyncSleeper = asyncio.sleep,
        clock: Clock | None = None,
    ) -> None:
        super().__init__(
            settings,
            provider_name=TMDB_PROVIDER,
            display_name="TMDB",
            session=session,
            sleeper=sleeper,
            clock=clock,
        )
        self.settings = settings

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return _TMDB_CAPABILITIES

    async def __aenter__(self) -> TMDBProvider:
        await self._get_session()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def search_movies(self, query: SearchQuery) -> tuple[SearchResult, ...]:
        payload = await self._request_json(
            ("search", "movie"), self._search_params(query, movie=True)
        )
        page: TMDBMovieSearchPage = self._parse_payload(TMDBMovieSearchPage, payload)
        return tuple(
            movie_search_result(entry, self.settings.image_base_url) for entry in page.results
        )

    async def search_series(self, query: SearchQuery) -> tuple[SearchResult, ...]:
        payload = await self._request_json(
            ("search", "tv"), self._search_params(query, movie=False)
        )
        page: TMDBSeriesSearchPage = self._parse_payload(TMDBSeriesSearchPage, payload)
        return tuple(
            series_search_result(entry, self.settings.image_base_url) for entry in page.results
        )

    async def get_movie(self, item_reference: ProviderReference) -> MovieDetails:
        raw_id = self._tmdb_id(item_reference)
        payload = await self._request_json(("movie", raw_id), self._details_params())
        movie: TMDBMoviePayload = self._parse_payload(TMDBMoviePayload, payload)
        return MovieDetails(
            reference=reference(movie.id),
            title=movie.title,
            original_title=movie.original_title,
            translated_title=movie.title,
            overview=movie.overview,
            release_date=movie.release_date,
            poster=artwork(movie.poster_path, ArtworkKind.POSTER, self.settings.image_base_url),
            backdrop=artwork(
                movie.backdrop_path, ArtworkKind.BACKDROP, self.settings.image_base_url
            ),
            genres=genres(movie.genres),
            original_language=movie.original_language,
            countries=countries(movie.production_countries),
            external_ids=external_ids(movie.id, movie.external_ids),
            runtime_minutes=movie.runtime,
        )

    async def get_series(self, item_reference: ProviderReference) -> SeriesDetails:
        raw_id = self._tmdb_id(item_reference)
        payload = await self._request_json(("tv", raw_id), self._details_params())
        series: TMDBSeriesPayload = self._parse_payload(TMDBSeriesPayload, payload)
        return SeriesDetails(
            reference=reference(series.id),
            title=series.name,
            original_title=series.original_name,
            translated_title=series.name,
            overview=series.overview,
            release_date=series.first_air_date,
            poster=artwork(series.poster_path, ArtworkKind.POSTER, self.settings.image_base_url),
            backdrop=artwork(
                series.backdrop_path, ArtworkKind.BACKDROP, self.settings.image_base_url
            ),
            genres=genres(series.genres),
            original_language=series.original_language,
            countries=countries(series.production_countries, series.origin_country),
            external_ids=external_ids(series.id, series.external_ids),
            season_count=series.number_of_seasons,
            episode_count=series.number_of_episodes,
        )

    async def get_season(
        self, series_reference: ProviderReference, season_number: int
    ) -> SeasonDetails:
        if season_number < 0:
            raise request_error(self.provider_name, "Season number must not be negative.")
        series_id = self._tmdb_id(series_reference)
        payload = await self._request_json(
            ("tv", series_id, "season", str(season_number)), self._details_params()
        )
        season: TMDBSeasonPayload = self._parse_payload(TMDBSeasonPayload, payload)
        series = reference(series_id)
        return SeasonDetails(
            reference=reference(season.id),
            series_reference=series,
            season_number=season.season_number,
            title=season.name,
            overview=season.overview,
            air_date=season.air_date,
            poster=artwork(season.poster_path, ArtworkKind.POSTER, self.settings.image_base_url),
            episodes=tuple(
                episode_details(episode, series, self.settings.image_base_url)
                for episode in season.episodes
            ),
            external_ids=external_ids(season.id, season.external_ids),
        )

    async def get_episode(
        self,
        series_reference: ProviderReference,
        season_number: int,
        episode_number: int,
    ) -> EpisodeDetails:
        if season_number < 0 or episode_number < 0:
            raise request_error(
                self.provider_name, "Season and episode numbers must not be negative."
            )
        series_id = self._tmdb_id(series_reference)
        payload = await self._request_json(
            ("tv", series_id, "season", str(season_number), "episode", str(episode_number)),
            self._details_params(),
        )
        episode: TMDBEpisodePayload = self._parse_payload(TMDBEpisodePayload, payload)
        return episode_details(episode, reference(series_id), self.settings.image_base_url)

    async def list_posters(
        self, item_reference: ProviderReference, media_kind: ProviderMediaKind
    ) -> tuple[ArtworkReference, ...]:
        """Return TMDB poster variants in a stable preference order."""

        raw_id = self._tmdb_id(item_reference)
        if media_kind is ProviderMediaKind.MOVIE:
            endpoint = "movie"
        elif media_kind is ProviderMediaKind.SERIES:
            endpoint = "tv"
        else:
            raise request_error(
                self.provider_name, "TMDB poster variants require a movie or series reference."
            )
        payload = await self._request_json((endpoint, raw_id, "images"), self._common_params())
        images: TMDBImagesPayload = self._parse_payload(TMDBImagesPayload, payload)
        preferred_language = self.settings.language.partition("-")[0].lower()
        variants = tuple(
            poster_artwork(image, self.settings.image_base_url) for image in images.posters
        )
        return tuple(
            sorted(
                variants,
                key=lambda artwork: (
                    0 if artwork.language == preferred_language else 1,
                    -(artwork.vote_count or 0),
                    -(artwork.vote_average or 0),
                    artwork.raw_path,
                ),
            )
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
        """Stream artwork into Katalog's temporary file using the shared session."""

        self._validate_artwork_reference(item_reference)
        return await self._download_artwork(
            URL(str(item_reference.source_url)), destination, maximum_size_bytes
        )

    async def _request_json(
        self, path_parts: tuple[str, ...], parameters: Mapping[str, str]
    ) -> Mapping[str, object]:
        return await self._fetch_json(
            self._endpoint_url(path_parts), self._api_headers(), parameters
        )

    def _api_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.api_token.get_secret_value()}",
            "User-Agent": KASANA_USER_AGENT,
            "Accept": "application/json",
        }

    def _search_params(self, query: SearchQuery, *, movie: bool) -> dict[str, str]:
        parameters = self._common_params()
        parameters["query"] = query.query
        parameters["include_adult"] = str(query.include_adult).lower()
        if query.year is not None:
            parameters["year" if movie else "first_air_date_year"] = str(query.year)
        return parameters

    def _details_params(self) -> dict[str, str]:
        parameters = self._common_params()
        parameters["append_to_response"] = "external_ids"
        return parameters

    def _common_params(self) -> dict[str, str]:
        return {"language": self.settings.language, "region": self.settings.region}

    def _endpoint_url(self, path_parts: tuple[str, ...]) -> URL:
        url = URL(str(self.settings.base_url).rstrip("/"))
        for part in path_parts:
            url = url / part
        return url

    def _tmdb_id(self, item_reference: ProviderReference) -> str:
        if item_reference.provider != self.provider_name:
            raise KourierError(
                ProviderErrorCategory.UNSUPPORTED_OPERATION,
                f"TMDB cannot resolve references from {item_reference.provider!r}.",
                provider=self.provider_name,
            )
        if not item_reference.raw_id.isdecimal():
            raise request_error(self.provider_name, "TMDB identifiers must be decimal numbers.")
        return item_reference.raw_id

    def _validate_artwork_reference(self, item_reference: ArtworkReference) -> None:
        if item_reference.provider != self.provider_name or item_reference.source_url is None:
            raise KourierError(
                ProviderErrorCategory.UNSUPPORTED_OPERATION,
                "TMDB artwork requires a TMDB reference with a source URL.",
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
                "TMDB returned an unexpected response payload.",
                provider=self.provider_name,
            ) from error

"""Bounded aiohttp TMDB client implementing Kourier provider contracts."""

from __future__ import annotations

import asyncio
import json
from asyncio.locks import Lock, Semaphore
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import aiohttp
from pydantic import BaseModel, ValidationError
from yarl import URL

from kasana.kourier.errors import KourierError
from kasana.kourier.settings import TMDBSettings
from kasana.kourier.tmdb.mapping import (
    artwork,
    countries,
    episode_details,
    external_ids,
    genres,
    movie_search_result,
    reference,
    series_search_result,
)
from kasana.kourier.tmdb.payloads import (
    TMDBEpisodePayload,
    TMDBMoviePayload,
    TMDBMovieSearchPage,
    TMDBSeasonPayload,
    TMDBSeriesPayload,
    TMDBSeriesSearchPage,
)
from kasana.kourier.tmdb.retry import (
    TMDB_PROVIDER,
    ArtworkDownloadResponse,
    AsyncSleeper,
    Clock,
    Response,
    RetryPolicy,
    http_error,
    request_error,
)
from kasana.shared.concurrency import run_blocking
from kasana.shared.metadata import (
    ArtworkContent,
    ArtworkDownload,
    ArtworkKind,
    ArtworkReference,
    EpisodeDetails,
    MovieDetails,
    ProviderCapability,
    ProviderErrorCategory,
    ProviderReference,
    SearchQuery,
    SearchResult,
    SeasonDetails,
    SeriesDetails,
)

_USER_AGENT = "Kasana/0.1 (+https://github.com/APasz/Kasana)"


class TMDBProvider:
    """Maps TMDB HTTP responses to Kourier's provider-neutral contracts."""

    def __init__(
        self,
        settings: TMDBSettings,
        *,
        session: aiohttp.ClientSession | None = None,
        sleeper: AsyncSleeper = asyncio.sleep,
        clock: Clock | None = None,
    ) -> None:
        self.settings = settings
        self._session = session
        self._owns_session = session is None
        self._session_lock = Lock()
        self._semaphore = Semaphore(settings.concurrency)
        self._timeout = aiohttp.ClientTimeout(total=settings.timeout_seconds)
        self._retry = RetryPolicy(settings, sleeper, clock or (lambda: datetime.now(UTC)))

    @property
    def provider_name(self) -> str:
        return TMDB_PROVIDER

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return frozenset(
            {
                ProviderCapability.SEARCH_MOVIES,
                ProviderCapability.SEARCH_SERIES,
                ProviderCapability.GET_MOVIE,
                ProviderCapability.GET_SERIES,
                ProviderCapability.GET_SEASON,
                ProviderCapability.GET_EPISODE,
                ProviderCapability.GET_ARTWORK,
            }
        )

    @property
    def session(self) -> aiohttp.ClientSession | None:
        return self._session

    def supports(self, capability: ProviderCapability) -> bool:
        return capability in self.capabilities

    async def __aenter__(self) -> TMDBProvider:
        await self._get_session()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()

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
            raise request_error("Season number must not be negative.")
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
            raise request_error("Season and episode numbers must not be negative.")
        series_id = self._tmdb_id(series_reference)
        payload = await self._request_json(
            ("tv", series_id, "season", str(season_number), "episode", str(episode_number)),
            self._details_params(),
        )
        episode: TMDBEpisodePayload = self._parse_payload(TMDBEpisodePayload, payload)
        return episode_details(episode, reference(series_id), self.settings.image_base_url)

    async def get_artwork(self, item_reference: ArtworkReference) -> ArtworkContent:
        self._validate_artwork_reference(item_reference)
        for attempt in range(self.settings.max_retries + 1):
            try:
                response = await self._request_artwork(URL(str(item_reference.source_url)))
            except TimeoutError as error:
                raise KourierError(
                    ProviderErrorCategory.TIMEOUT,
                    "TMDB artwork request timed out.",
                    provider=self.provider_name,
                ) from error
            except aiohttp.ClientConnectionError as error:
                if await self._retry.connection_error(attempt):
                    continue
                raise KourierError(
                    ProviderErrorCategory.TRANSIENT,
                    "TMDB artwork connection failed after retries.",
                    provider=self.provider_name,
                ) from error
            except aiohttp.ClientError as error:
                raise KourierError(
                    ProviderErrorCategory.REQUEST_FAILED,
                    "TMDB artwork request failed.",
                    provider=self.provider_name,
                ) from error
            if 200 <= response.status < 300:
                return ArtworkContent(
                    reference=item_reference,
                    content=response.body,
                    media_type=response.headers.get("Content-Type"),
                )
            if await self._retryable_status(attempt, response.status, response.headers):
                continue
            raise self._response_error(response.status)
        raise RuntimeError("TMDB artwork retry loop ended unexpectedly.")

    async def download_artwork(
        self,
        item_reference: ArtworkReference,
        destination: Path,
        *,
        maximum_size_bytes: int,
    ) -> ArtworkDownload:
        """Stream artwork into Katalog's temporary file using the shared session."""

        if maximum_size_bytes < 1:
            raise ValueError("Artwork maximum size must be positive.")
        self._validate_artwork_reference(item_reference)
        for attempt in range(self.settings.max_retries + 1):
            try:
                response = await self._stream_artwork(
                    URL(str(item_reference.source_url)), destination, maximum_size_bytes
                )
            except TimeoutError as error:
                raise KourierError(
                    ProviderErrorCategory.TIMEOUT,
                    "TMDB artwork request timed out.",
                    provider=self.provider_name,
                ) from error
            except aiohttp.ClientConnectionError as error:
                if await self._retry.connection_error(attempt):
                    continue
                raise KourierError(
                    ProviderErrorCategory.TRANSIENT,
                    "TMDB artwork connection failed after retries.",
                    provider=self.provider_name,
                ) from error
            except aiohttp.ClientError as error:
                raise KourierError(
                    ProviderErrorCategory.REQUEST_FAILED,
                    "TMDB artwork request failed.",
                    provider=self.provider_name,
                ) from error
            if 200 <= response.status < 300:
                return ArtworkDownload(
                    content_type=response.content_type, size_bytes=response.size_bytes
                )
            if await self._retryable_status(attempt, response.status, response.headers):
                continue
            raise self._response_error(response.status)
        raise RuntimeError("TMDB artwork retry loop ended unexpectedly.")

    async def _request_json(
        self, path_parts: tuple[str, ...], parameters: Mapping[str, str]
    ) -> Mapping[str, object]:
        for attempt in range(self.settings.max_retries + 1):
            try:
                response = await self._request(path_parts, parameters)
            except TimeoutError as error:
                raise KourierError(
                    ProviderErrorCategory.TIMEOUT,
                    "TMDB request timed out.",
                    provider=self.provider_name,
                ) from error
            except aiohttp.ClientConnectionError as error:
                if await self._retry.connection_error(attempt):
                    continue
                raise KourierError(
                    ProviderErrorCategory.TRANSIENT,
                    "TMDB connection failed after retries.",
                    provider=self.provider_name,
                ) from error
            except aiohttp.ClientError as error:
                raise KourierError(
                    ProviderErrorCategory.REQUEST_FAILED,
                    "TMDB request failed.",
                    provider=self.provider_name,
                ) from error
            if 200 <= response.status < 300:
                return decode_json(response.body)
            if await self._retryable_status(attempt, response.status, response.headers):
                continue
            raise self._response_error(response.status)
        raise RuntimeError("TMDB retry loop ended unexpectedly.")

    async def _retryable_status(
        self, attempt: int, status: int, headers: Mapping[str, str]
    ) -> bool:
        if status == 429 or status >= 500:
            return await self._retry.status(attempt, headers)
        return False

    def _response_error(self, status: int) -> KourierError:
        if status == 429:
            return http_error(ProviderErrorCategory.RATE_LIMITED, status)
        if status >= 500:
            return http_error(ProviderErrorCategory.TRANSIENT, status)
        if status in {401, 403}:
            return http_error(ProviderErrorCategory.AUTHENTICATION, status)
        if status == 404:
            return http_error(ProviderErrorCategory.NOT_FOUND, status)
        return http_error(ProviderErrorCategory.REQUEST_FAILED, status)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is not None:
            return self._session
        async with self._session_lock:
            if self._session is None:
                self._session = aiohttp.ClientSession(timeout=self._timeout)
            return self._session

    async def _request(
        self, path_parts: tuple[str, ...], parameters: Mapping[str, str]
    ) -> Response:
        session = await self._get_session()
        headers = {
            "Authorization": f"Bearer {self.settings.api_token.get_secret_value()}",
            "User-Agent": _USER_AGENT,
            "Accept": "application/json",
        }
        async with self._semaphore:
            async with session.get(
                self._endpoint_url(path_parts),
                params=parameters,
                headers=headers,
                timeout=self._timeout,
            ) as reply:
                return Response(reply.status, dict(reply.headers), await reply.read())

    async def _request_artwork(self, url: URL) -> Response:
        session = await self._get_session()
        headers = {"User-Agent": _USER_AGENT, "Accept": "image/*"}
        async with self._semaphore:
            async with session.get(url, headers=headers, timeout=self._timeout) as reply:
                return Response(reply.status, dict(reply.headers), await reply.read())

    async def _stream_artwork(
        self, url: URL, destination: Path, maximum_size_bytes: int
    ) -> ArtworkDownloadResponse:
        session = await self._get_session()
        headers = {"User-Agent": _USER_AGENT, "Accept": "image/*"}
        async with self._semaphore:
            async with session.get(url, headers=headers, timeout=self._timeout) as reply:
                response_headers = dict(reply.headers)
                if not 200 <= reply.status < 300:
                    return ArtworkDownloadResponse(reply.status, response_headers, None, 0)
                await run_blocking(truncate_file, destination)
                size_bytes = 0
                async for chunk in reply.content.iter_chunked(64 * 1024):
                    size_bytes += len(chunk)
                    if size_bytes > maximum_size_bytes:
                        raise KourierError(
                            ProviderErrorCategory.REQUEST_FAILED,
                            f"Artwork response exceeds {maximum_size_bytes} bytes.",
                            provider=self.provider_name,
                        )
                    await run_blocking(append_file, destination, chunk)
                return ArtworkDownloadResponse(
                    reply.status,
                    response_headers,
                    response_headers.get("Content-Type"),
                    size_bytes,
                )

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
            raise request_error("TMDB identifiers must be decimal numbers.")
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


def decode_json(body: bytes) -> Mapping[str, object]:
    try:
        decoded: object = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise KourierError(
            ProviderErrorCategory.MALFORMED_RESPONSE,
            "TMDB returned invalid JSON.",
            provider=TMDB_PROVIDER,
        ) from error
    if not isinstance(decoded, dict):
        raise KourierError(
            ProviderErrorCategory.MALFORMED_RESPONSE,
            "TMDB returned a non-object JSON response.",
            provider=TMDB_PROVIDER,
        )
    payload = cast(dict[object, object], decoded)
    if not all(isinstance(key, str) for key in payload):
        raise KourierError(
            ProviderErrorCategory.MALFORMED_RESPONSE,
            "TMDB returned a JSON object with non-string keys.",
            provider=TMDB_PROVIDER,
        )
    return cast(dict[str, object], payload)


def truncate_file(path: Path) -> None:
    path.write_bytes(b"")


def append_file(path: Path, content: bytes) -> None:
    with path.open("ab") as file:
        file.write(content)

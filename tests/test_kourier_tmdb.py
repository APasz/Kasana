from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import aiohttp
import pytest
from pydantic import AnyHttpUrl
from yarl import URL

from kasana.kourier.errors import KourierError
from kasana.kourier.http import RequestPacer
from kasana.kourier.settings import TMDBSettings
from kasana.kourier.tmdb import TMDBProvider
from kasana.kourier.tmdb.mapping import TMDBImageUrls, configured_image_urls
from kasana.kourier.tmdb.payloads import TMDBImageConfiguration
from kasana.shared.metadata import (
    ArtworkKind,
    ArtworkReference,
    ProviderCapability,
    ProviderErrorCategory,
    ProviderMediaKind,
    ProviderReference,
    SearchQuery,
)

type Sleeper = Callable[[float], Awaitable[None]]
type Clock = Callable[[], datetime]


async def _ignore_pacer_delay(delay: float) -> None:
    del delay


_TEST_IMAGE_URLS = TMDBImageUrls(
    poster=AnyHttpUrl("https://images.tmdb.test/w780"),
    backdrop=AnyHttpUrl("https://images.tmdb.test/w780"),
    still=AnyHttpUrl("https://images.tmdb.test/w300"),
)


def _test_request_pacer() -> RequestPacer:
    return RequestPacer(1_000_000_000.0, sleeper=_ignore_pacer_delay)


class _RecordingPacer(RequestPacer):
    def __init__(self) -> None:
        super().__init__(1.0)
        self.deferred: list[float] = []

    async def wait(self) -> None:
        return None

    async def defer(self, delay_seconds: float) -> None:
        self.deferred.append(delay_seconds)


@dataclass(frozen=True)
class _FakeResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes

    async def read(self) -> bytes:
        return self.body

    @property
    def content(self) -> _FakeContent:
        return _FakeContent(self.body)


@dataclass(frozen=True)
class _FakeContent:
    body: bytes

    async def iter_chunked(self, chunk_size: int):
        for start in range(0, len(self.body), chunk_size):
            yield self.body[start : start + chunk_size]


class _FakeRequest:
    def __init__(self, outcome: _FakeResponse | BaseException) -> None:
        self.outcome = outcome

    async def __aenter__(self) -> _FakeResponse:
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    async def __aexit__(self, *_: object) -> None:
        return None


@dataclass
class _FakeSession:
    outcomes: list[_FakeResponse | BaseException]
    calls: list[tuple[URL, Mapping[str, str], Mapping[str, str]]] = field(default_factory=list)
    closed: bool = False

    def get(
        self,
        url: URL,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: aiohttp.ClientTimeout,
    ) -> _FakeRequest:
        del timeout
        self.calls.append((url, params or {}, headers or {}))
        return _FakeRequest(self.outcomes.pop(0))

    async def close(self) -> None:
        self.closed = True


def _json_response(
    value: object, *, status: int = 200, headers: Mapping[str, str] = {}
) -> _FakeResponse:
    return _FakeResponse(status=status, headers=headers, body=json.dumps(value).encode())


def _settings(**changes: object) -> TMDBSettings:
    values: dict[str, object] = {
        "api_token": "test-token",
        "base_url": "https://tmdb.test/3",
        "image_target_width": 780,
        "use_original_images": False,
        "language": "en-AU",
        "region": "AU",
        "timeout_seconds": 0.5,
        "concurrency": 2,
        "requests_per_second": 10.0,
        "max_retries": 2,
        "retry_backoff_seconds": 0.1,
        "max_backoff_seconds": 1.0,
    }
    values.update(changes)
    return TMDBSettings.model_validate(values)


def _provider(
    outcomes: list[_FakeResponse | BaseException],
    *,
    sleeper: Sleeper = asyncio.sleep,
    clock: Clock | None = None,
    request_pacer: RequestPacer | None = None,
    image_urls: TMDBImageUrls | None = _TEST_IMAGE_URLS,
    **settings: object,
) -> tuple[TMDBProvider, _FakeSession]:
    session = _FakeSession(outcomes)
    provider = TMDBProvider(
        _settings(**settings),
        session=cast(aiohttp.ClientSession, session),
        sleeper=sleeper,
        clock=clock,
        request_pacer=request_pacer or _test_request_pacer(),
        image_urls=image_urls,
    )
    return provider, session


async def test_successful_search_and_detail_mapping() -> None:
    provider, session = _provider(
        [
            _json_response(
                {
                    "results": [
                        {
                            "id": 11,
                            "title": "Spirited Away",
                            "original_title": "千と千尋の神隠し",
                            "overview": "A journey.",
                            "release_date": "2001-07-20",
                            "poster_path": "/poster.jpg",
                            "backdrop_path": "/backdrop.jpg",
                            "original_language": "ja",
                        }
                    ]
                }
            ),
            _json_response(
                {
                    "results": [
                        {
                            "id": 22,
                            "name": "Stargate SG-1",
                            "original_name": "Stargate SG-1",
                            "first_air_date": "1997-07-27",
                        }
                    ]
                }
            ),
            _json_response(
                {
                    "id": 11,
                    "title": "Spirited Away",
                    "original_title": "千と千尋の神隠し",
                    "overview": "A journey.",
                    "release_date": "2001-07-20",
                    "poster_path": "/poster.jpg",
                    "backdrop_path": "/backdrop.jpg",
                    "genres": [{"name": "Animation"}],
                    "original_language": "ja",
                    "production_countries": [{"iso_3166_1": "JP", "name": "Japan"}],
                    "external_ids": {"imdb_id": "tt0245429"},
                    "runtime": 125,
                }
            ),
            _json_response(
                {
                    "id": 22,
                    "name": "Stargate SG-1",
                    "original_name": "Stargate SG-1",
                    "overview": "A team explores the galaxy.",
                    "first_air_date": "1997-07-27",
                    "genres": [{"name": "Sci-Fi & Fantasy"}],
                    "origin_country": ["US"],
                    "external_ids": {"tvdb_id": 72449},
                    "number_of_seasons": 10,
                    "number_of_episodes": 214,
                }
            ),
            _json_response(
                {
                    "id": 33,
                    "name": "Season 1",
                    "season_number": 1,
                    "episodes": [
                        {
                            "id": 44,
                            "name": "Children of the Gods",
                            "season_number": 1,
                            "episode_number": 1,
                            "still_path": "/still.jpg",
                        }
                    ],
                }
            ),
            _json_response(
                {
                    "id": 44,
                    "name": "Children of the Gods",
                    "season_number": 1,
                    "episode_number": 1,
                }
            ),
        ]
    )

    movies = await provider.search_movies(SearchQuery(query="Spirited Away", year=2001))
    series_results = await provider.search_series(SearchQuery(query="Stargate"))
    movie = await provider.get_movie(movies[0].reference)
    series = await provider.get_series(series_results[0].reference)
    season = await provider.get_season(series.reference, 1)
    episode = await provider.get_episode(series.reference, 1, 1)

    assert movies[0].media_kind is ProviderMediaKind.MOVIE
    assert movies[0].poster is not None
    assert movies[0].poster.kind is ArtworkKind.POSTER
    assert str(movies[0].poster.source_url) == "https://images.tmdb.test/w780/poster.jpg"
    assert movie.release_date is not None
    assert movie.release_date.isoformat() == "2001-07-20"
    assert movie.countries[0].name == "Japan"
    assert {identifier.namespace for identifier in movie.external_ids} == {"tmdb", "imdb"}
    assert series.countries[0].code == "US"
    assert series.episode_count == 214
    assert season.episodes[0].reference.raw_id == "44"
    assert episode.season_number == 1
    assert provider.supports(ProviderCapability.GET_ARTWORK) is True
    assert [url.path for url, _, _ in session.calls] == [
        "/3/search/movie",
        "/3/search/tv",
        "/3/movie/11",
        "/3/tv/22",
        "/3/tv/22/season/1",
        "/3/tv/22/season/1/episode/1",
    ]
    assert session.calls[0][2]["Authorization"] == "Bearer test-token"
    assert session.calls[0][2]["User-Agent"].startswith("Kasana/")
    assert session.calls[0][1]["language"] == "en-AU"
    assert session.calls[0][1]["year"] == "2001"


async def test_poster_variants_prefer_the_configured_language_and_keep_picker_metadata() -> None:
    provider, session = _provider(
        [
            _json_response(
                {
                    "posters": [
                        {
                            "file_path": "/japanese.jpg",
                            "iso_639_1": "ja",
                            "width": 1000,
                            "height": 1500,
                            "vote_average": 9.0,
                            "vote_count": 500,
                        },
                        {
                            "file_path": "/english.jpg",
                            "iso_639_1": "en",
                            "width": 2000,
                            "height": 3000,
                            "vote_average": 7.5,
                            "vote_count": 20,
                        },
                    ]
                }
            )
        ]
    )

    posters = await provider.list_posters(
        ProviderReference(provider="tmdb", raw_id="11"), ProviderMediaKind.MOVIE
    )

    assert [poster.raw_path for poster in posters] == ["/english.jpg", "/japanese.jpg"]
    assert posters[0].language == "en"
    assert (posters[0].width, posters[0].height) == (2000, 3000)
    assert (posters[0].vote_average, posters[0].vote_count) == (7.5, 20)
    assert str(posters[0].source_url) == "https://images.tmdb.test/w780/english.jpg"
    assert session.calls[0][0].path == "/3/movie/11/images"
    assert session.calls[0][1] == {
        "language": "en-AU",
        "include_image_language": "en,null",
    }


def test_configured_image_urls_choose_efficient_renditions_unless_original_is_opted_in() -> None:
    configuration = TMDBImageConfiguration.model_validate(
        {
            "secure_base_url": "https://images.tmdb.test/t/p/",
            "poster_sizes": ["w92", "w500", "original"],
            "backdrop_sizes": ["w300", "w780", "original"],
            "still_sizes": ["w92", "w300", "original"],
        }
    )

    picker_urls = configured_image_urls(configuration, target_width=600, use_original_images=False)
    original_urls = configured_image_urls(configuration, target_width=600, use_original_images=True)

    assert str(picker_urls.poster) == "https://images.tmdb.test/t/p/w500"
    assert str(picker_urls.backdrop) == "https://images.tmdb.test/t/p/w300"
    assert str(picker_urls.still) == "https://images.tmdb.test/t/p/w300"
    assert str(original_urls.poster) == "https://images.tmdb.test/t/p/original"


async def test_image_urls_are_loaded_once_from_tmdb_configuration() -> None:
    provider, session = _provider(
        [
            _json_response(
                {
                    "results": [
                        {
                            "id": 11,
                            "title": "Spirited Away",
                            "poster_path": "/poster.jpg",
                            "backdrop_path": "/backdrop.jpg",
                        }
                    ]
                }
            ),
            _json_response(
                {
                    "images": {
                        "secure_base_url": "https://images.tmdb.test/t/p/",
                        "poster_sizes": ["w92", "w500", "original"],
                        "backdrop_sizes": ["w300", "w780", "original"],
                        "still_sizes": ["w92", "w300", "original"],
                    }
                }
            ),
            _json_response({"results": []}),
        ],
        image_urls=None,
        image_target_width=600,
    )

    results = await provider.search_movies(SearchQuery(query="Spirited Away"))
    subsequent_results = await provider.search_movies(SearchQuery(query="Ponyo"))

    assert results[0].poster is not None
    assert results[0].backdrop is not None
    assert subsequent_results == ()
    assert str(results[0].poster.source_url) == "https://images.tmdb.test/t/p/w500/poster.jpg"
    assert str(results[0].backdrop.source_url) == "https://images.tmdb.test/t/p/w300/backdrop.jpg"
    assert [url.path for url, _, _ in session.calls] == [
        "/3/search/movie",
        "/3/configuration",
        "/3/search/movie",
    ]


async def test_empty_search_does_not_request_image_configuration() -> None:
    provider, session = _provider([_json_response({"results": []})], image_urls=None)

    assert await provider.search_movies(SearchQuery(query="Missing")) == ()
    assert [url.path for url, _, _ in session.calls] == ["/3/search/movie"]


async def test_empty_poster_gallery_does_not_request_image_configuration() -> None:
    provider, session = _provider([_json_response({"posters": []})], image_urls=None)

    assert (
        await provider.list_posters(
            ProviderReference(provider="tmdb", raw_id="11"), ProviderMediaKind.MOVIE
        )
        == ()
    )
    assert [url.path for url, _, _ in session.calls] == ["/3/movie/11/images"]


async def test_malformed_payload_is_a_typed_error() -> None:
    provider, _ = _provider([_json_response({"results": [{"id": 1}]})])

    with pytest.raises(KourierError) as error:
        await provider.search_movies(SearchQuery(query="Missing title"))

    assert error.value.category is ProviderErrorCategory.MALFORMED_RESPONSE

    provider, _ = _provider([_FakeResponse(status=200, headers={}, body=b"not-json")])
    with pytest.raises(KourierError) as invalid_json_error:
        await provider.search_movies(SearchQuery(query="Broken JSON"))
    assert invalid_json_error.value.category is ProviderErrorCategory.MALFORMED_RESPONSE

    provider, _ = _provider(
        [
            _json_response(
                {
                    "posters": [
                        {
                            "file_path": "  ",
                            "width": 1,
                            "height": 1,
                            "vote_average": 0,
                            "vote_count": 0,
                        }
                    ]
                }
            )
        ]
    )
    with pytest.raises(KourierError) as blank_path_error:
        await provider.list_posters(
            ProviderReference(provider="tmdb", raw_id="11"), ProviderMediaKind.MOVIE
        )
    assert blank_path_error.value.category is ProviderErrorCategory.MALFORMED_RESPONSE


async def test_artwork_reuses_the_provider_session_and_maps_bytes() -> None:
    provider, session = _provider(
        [_FakeResponse(status=200, headers={"content-type": "image/jpeg"}, body=b"image")]
    )
    reference = ArtworkReference(
        provider="tmdb",
        kind=ArtworkKind.POSTER,
        raw_path="/poster.jpg",
        source_url=AnyHttpUrl("https://images.tmdb.test/original/poster.jpg"),
    )

    artwork = await provider.get_artwork(reference)

    assert artwork.reference == reference
    assert artwork.content == b"image"
    assert artwork.media_type == "image/jpeg"
    assert session.calls[0][0].path == "/original/poster.jpg"
    assert session.calls[0][2]["Accept"] == "image/*"


async def test_artwork_streaming_uses_a_temporary_destination(tmp_path: Path) -> None:
    provider, _ = _provider(
        [_FakeResponse(status=200, headers={"content-type": "image/png"}, body=b"chunked")]
    )
    reference = ArtworkReference(
        provider="tmdb",
        kind=ArtworkKind.POSTER,
        raw_path="/poster.png",
        source_url=AnyHttpUrl("https://images.tmdb.test/original/poster.png"),
    )
    destination = tmp_path / "artwork.tmp"

    download = await provider.download_artwork(reference, destination, maximum_size_bytes=1024)

    assert destination.read_bytes() == b"chunked"
    assert download.content_type == "image/png"
    assert download.size_bytes == len(b"chunked")


@pytest.mark.parametrize(
    ("status", "category"),
    [
        (401, ProviderErrorCategory.AUTHENTICATION),
        (404, ProviderErrorCategory.NOT_FOUND),
    ],
)
async def test_non_retryable_http_errors_are_typed(
    status: int, category: ProviderErrorCategory
) -> None:
    provider, session = _provider([_json_response({}, status=status)])

    with pytest.raises(KourierError) as error:
        await provider.get_movie(ProviderReference(provider="tmdb", raw_id="11"))

    assert error.value.category is category
    assert error.value.status_code == status
    assert len(session.calls) == 1


async def test_rate_limiting_honours_retry_after() -> None:
    delays: list[float] = []

    async def sleeper(delay: float) -> None:
        delays.append(delay)

    provider, session = _provider(
        [
            _json_response(
                {},
                status=429,
                headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"},
            ),
            _json_response({"results": []}),
        ],
        sleeper=sleeper,
        clock=lambda: datetime(2015, 10, 21, 7, 27, 58, tzinfo=UTC),
    )

    assert await provider.search_movies(SearchQuery(query="Stargate")) == ()
    assert len(session.calls) == 2
    assert delays == [2.0]

    provider, session = _provider(
        [_json_response({}, status=429), _json_response({}, status=429)],
        max_retries=1,
    )
    with pytest.raises(KourierError) as rate_limit_error:
        await provider.search_movies(SearchQuery(query="Still limited"))
    assert rate_limit_error.value.category is ProviderErrorCategory.RATE_LIMITED
    assert len(session.calls) == 2


async def test_rate_limit_cooldown_is_shared_even_when_no_retry_remains() -> None:
    pacer = _RecordingPacer()
    provider, _ = _provider(
        [_json_response({}, status=429, headers={"Retry-After": "3"})],
        max_retries=0,
        request_pacer=pacer,
    )

    with pytest.raises(KourierError) as error:
        await provider.search_movies(SearchQuery(query="Still limited"))

    assert error.value.category is ProviderErrorCategory.RATE_LIMITED
    assert pacer.deferred == [3.0]


async def test_transient_status_and_connection_errors_retry_with_bounded_backoff() -> None:
    delays: list[float] = []

    async def sleeper(delay: float) -> None:
        delays.append(delay)

    provider, session = _provider(
        [
            _json_response({}, status=503),
            _json_response({}, status=503),
            _json_response({"results": []}),
            aiohttp.ClientConnectionError("offline"),
            aiohttp.ClientConnectionError("offline"),
            _json_response({"results": []}),
        ],
        sleeper=sleeper,
        max_backoff_seconds=0.15,
    )

    assert await provider.search_movies(SearchQuery(query="Stargate")) == ()
    assert await provider.search_series(SearchQuery(query="Stargate")) == ()

    assert len(session.calls) == 6
    assert delays == [0.1, 0.15, 0.1, 0.15]


async def test_timeout_cancellation_and_owned_session_cleanup() -> None:
    provider, _ = _provider([TimeoutError()])
    with pytest.raises(KourierError) as timeout_error:
        await provider.search_movies(SearchQuery(query="Slow response"))
    assert timeout_error.value.category is ProviderErrorCategory.TIMEOUT

    provider, _ = _provider([])

    async def wait_for_cancellation(*_: object) -> object:
        await asyncio.Event().wait()
        msg = "Unreachable after cancellation."
        raise RuntimeError(msg)

    provider._request = wait_for_cancellation  # type: ignore[method-assign]
    task = asyncio.create_task(provider.search_movies(SearchQuery(query="Cancel me")))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    owned_provider = TMDBProvider(_settings())
    await owned_provider.__aenter__()
    session = owned_provider.session
    assert session is not None
    await owned_provider.close()
    assert session.closed

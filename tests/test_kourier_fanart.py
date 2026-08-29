from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import cast

import aiohttp
import pytest
from pydantic import AnyHttpUrl
from yarl import URL

from kasana.kourier.errors import KourierError
from kasana.kourier.fanart import FanartProvider
from kasana.kourier.http import RequestPacer
from kasana.kourier.settings import FanartSettings
from kasana.shared.metadata import (
    ArtworkKind,
    ArtworkReference,
    ExternalIdentifier,
    PosterLookup,
    ProviderErrorCategory,
    ProviderMediaKind,
    ProviderReference,
)

type Sleeper = Callable[[float], Awaitable[None]]
type Clock = Callable[[], datetime]


async def _ignore_pacer_delay(delay: float) -> None:
    del delay


def _test_request_pacer() -> RequestPacer:
    return RequestPacer(1_000_000_000.0, sleeper=_ignore_pacer_delay)


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
    calls: list[tuple[URL, Mapping[str, str]]] = field(default_factory=list)
    closed: bool = False

    def get(
        self,
        url: URL,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: aiohttp.ClientTimeout,
    ) -> _FakeRequest:
        del timeout
        self.calls.append((url, headers or {}))
        return _FakeRequest(self.outcomes.pop(0))

    async def close(self) -> None:
        self.closed = True


def _json_response(
    value: object, *, status: int = 200, headers: Mapping[str, str] | None = None
) -> _FakeResponse:
    return _FakeResponse(status=status, headers=headers or {}, body=json.dumps(value).encode())


def _settings(**changes: object) -> FanartSettings:
    values: dict[str, object] = {
        "api_key": "project-key",
        "client_key": "personal-key",
        "base_url": "https://fanart.test/v3.2",
        "language": "en-AU",
        "timeout_seconds": 0.5,
        "concurrency": 2,
        "requests_per_second": 4.0,
        "max_retries": 2,
        "retry_backoff_seconds": 0.1,
        "max_backoff_seconds": 1.0,
    }
    values.update(changes)
    return FanartSettings.model_validate(values)


def _provider(
    outcomes: list[_FakeResponse | BaseException],
    *,
    sleeper: Sleeper = asyncio.sleep,
    clock: Clock | None = None,
    **settings: object,
) -> tuple[FanartProvider, _FakeSession]:
    session = _FakeSession(outcomes)
    provider = FanartProvider(
        _settings(**settings),
        session=cast(aiohttp.ClientSession, session),
        sleeper=sleeper,
        clock=clock,
        request_pacer=_test_request_pacer(),
    )
    return provider, session


async def test_movie_posters_use_tmdb_identity_and_retain_picker_metadata() -> None:
    provider, session = _provider(
        [
            _json_response(
                {
                    "movieposter": [
                        {
                            "id": "japanese",
                            "url": "https://assets.fanart.test/poster-ja.jpg",
                            "lang": "ja",
                            "likes": "100",
                            "width": "1000",
                            "height": "1500",
                        },
                        {
                            "id": "english",
                            "url": "https://assets.fanart.test/poster-en.jpg",
                            "lang": "en",
                            "likes": "10",
                            "width": "2000",
                            "height": "3000",
                        },
                        {
                            "id": "textless",
                            "url": "https://assets.fanart.test/poster-textless.jpg",
                            "lang": "00",
                            "likes": "20",
                            "width": "1600",
                            "height": "2400",
                        },
                    ]
                }
            )
        ]
    )

    listing = await provider.list_posters_by_external_id(
        PosterLookup(
            reference=ProviderReference(provider="tmdb", raw_id="550"),
            media_kind=ProviderMediaKind.MOVIE,
        )
    )

    assert listing is not None
    assert (listing.provider, listing.provider_id) == ("fanart", "tmdb:550")
    assert [poster.raw_path for poster in listing.posters] == ["english", "japanese", "textless"]
    assert listing.posters[0].language == "en"
    assert (listing.posters[0].width, listing.posters[0].height) == (2000, 3000)
    assert listing.posters[0].vote_count == 10
    assert listing.posters[2].language is None
    assert session.calls[0][0].path == "/v3.2/movies/550"
    assert session.calls[0][1]["api-key"] == "project-key"
    assert session.calls[0][1]["client-key"] == "personal-key"


async def test_season_posters_use_tvdb_identity_and_filter_to_the_local_season() -> None:
    provider, session = _provider(
        [
            _json_response(
                {
                    "seasonposter": [
                        {
                            "id": "season-five-japanese",
                            "url": "https://assets.fanart.test/season-five-ja.jpg",
                            "lang": "ja",
                            "likes": "100",
                            "season": "5",
                            "width": "1000",
                            "height": "1426",
                        },
                        {
                            "id": "season-five-english",
                            "url": "https://assets.fanart.test/season-five-en.jpg",
                            "lang": "en",
                            "likes": "10",
                            "season": "5",
                            "width": "2000",
                            "height": "2852",
                        },
                        {
                            "id": "season-four",
                            "url": "https://assets.fanart.test/season-four.jpg",
                            "lang": "en",
                            "likes": "500",
                            "season": "4",
                        },
                    ]
                }
            )
        ]
    )

    listing = await provider.list_posters_by_external_id(
        PosterLookup(
            reference=ProviderReference(provider="tvdb", raw_id="season-250142"),
            media_kind=ProviderMediaKind.SEASON,
            external_ids=(ExternalIdentifier(namespace="tvdb", value="81189"),),
            season_number=5,
        )
    )

    assert listing is not None
    assert (listing.provider, listing.provider_id) == ("fanart", "tvdb:81189:season:5")
    assert [poster.raw_path for poster in listing.posters] == [
        "season-five-english",
        "season-five-japanese",
    ]
    assert (listing.posters[0].width, listing.posters[0].height) == (2000, 2852)
    assert session.calls[0][0].path == "/v3.2/tv/81189"


async def test_missing_movie_is_an_authoritative_empty_listing() -> None:
    provider, _ = _provider([_json_response({"status": "not found"}, status=404)])

    listing = await provider.list_posters_by_external_id(
        PosterLookup(
            reference=ProviderReference(provider="tmdb", raw_id="550"),
            media_kind=ProviderMediaKind.MOVIE,
        )
    )

    assert listing is not None
    assert (listing.provider, listing.provider_id, listing.posters) == ("fanart", "tmdb:550", ())


async def test_personal_key_only_lookup_falls_back_to_an_imdb_identifier() -> None:
    provider, session = _provider(
        [
            _json_response(
                {
                    "movieposter": [
                        {
                            "id": "fanart-poster",
                            "url": "https://assets.fanart.test/poster.jpg",
                        }
                    ]
                }
            )
        ],
        api_key=None,
    )

    listing = await provider.list_posters_by_external_id(
        PosterLookup(
            reference=ProviderReference(provider="other", raw_id="other-id"),
            media_kind=ProviderMediaKind.MOVIE,
            external_ids=(ExternalIdentifier(namespace="imdb", value="tt0137523"),),
        )
    )

    assert listing is not None
    assert listing.provider_id == "imdb:tt0137523"
    assert session.calls[0][0].path == "/v3.2/movies/tt0137523"
    assert "api-key" not in session.calls[0][1]
    assert session.calls[0][1]["client-key"] == "personal-key"


async def test_series_lookup_does_not_mislabel_season_art_as_a_series_poster() -> None:
    provider, session = _provider([])

    listing = await provider.list_posters_by_external_id(
        PosterLookup(
            reference=ProviderReference(provider="tmdb", raw_id="1399"),
            media_kind=ProviderMediaKind.SERIES,
        )
    )

    assert listing is None
    assert session.calls == []


async def test_artwork_download_uses_the_shared_session(tmp_path: Path) -> None:
    provider, session = _provider(
        [_FakeResponse(status=200, headers={"Content-Type": "image/png"}, body=b"chunked")]
    )
    reference = ArtworkReference(
        provider="fanart",
        kind=ArtworkKind.POSTER,
        raw_path="poster",
        source_url=AnyHttpUrl("https://assets.fanart.test/poster.png"),
    )
    destination = tmp_path / "artwork.tmp"

    download = await provider.download_artwork(reference, destination, maximum_size_bytes=1024)

    assert download.content_type == "image/png"
    assert download.size_bytes == len(b"chunked")
    assert destination.read_bytes() == b"chunked"
    assert session.calls[0][0].path == "/poster.png"
    assert session.calls[0][1]["Accept"] == "image/*"


async def test_artwork_content_does_not_send_credentials_to_the_asset_host() -> None:
    provider, session = _provider(
        [_FakeResponse(status=200, headers={"Content-Type": "image/png"}, body=b"image")]
    )
    reference = ArtworkReference(
        provider="fanart",
        kind=ArtworkKind.POSTER,
        raw_path="poster",
        source_url=AnyHttpUrl("https://assets.fanart.test/poster.png"),
    )

    content = await provider.get_artwork(reference)

    assert content.content == b"image"
    assert content.media_type == "image/png"
    assert session.calls[0][1]["Accept"] == "image/*"
    assert "api-key" not in session.calls[0][1]
    assert "client-key" not in session.calls[0][1]


async def test_malformed_movie_response_is_a_typed_error() -> None:
    provider, _ = _provider([_json_response({"movieposter": [{"id": "missing-url"}]})])

    with pytest.raises(KourierError) as error:
        await provider.list_posters_by_external_id(
            PosterLookup(
                reference=ProviderReference(provider="tmdb", raw_id="550"),
                media_kind=ProviderMediaKind.MOVIE,
            )
        )

    assert error.value.category is ProviderErrorCategory.MALFORMED_RESPONSE


async def test_blank_image_id_is_a_typed_error() -> None:
    provider, _ = _provider(
        [
            _json_response(
                {
                    "movieposter": [
                        {
                            "id": "  ",
                            "url": "https://assets.fanart.test/poster.jpg",
                        }
                    ]
                }
            )
        ]
    )

    with pytest.raises(KourierError) as error:
        await provider.list_posters_by_external_id(
            PosterLookup(
                reference=ProviderReference(provider="tmdb", raw_id="550"),
                media_kind=ProviderMediaKind.MOVIE,
            )
        )

    assert error.value.category is ProviderErrorCategory.MALFORMED_RESPONSE


@pytest.mark.parametrize(
    "poster",
    (
        {
            "id": "fanart-poster",
            "url": "https://assets.fanart.test/poster.jpg",
            "width": 20_001,
        },
        {"id": "x" * 501, "url": "https://assets.fanart.test/poster.jpg"},
    ),
)
async def test_out_of_contract_poster_metadata_is_a_typed_error(poster: dict[str, object]) -> None:
    provider, _ = _provider([_json_response({"movieposter": [poster]})])

    with pytest.raises(KourierError) as error:
        await provider.list_posters_by_external_id(
            PosterLookup(
                reference=ProviderReference(provider="tmdb", raw_id="550"),
                media_kind=ProviderMediaKind.MOVIE,
            )
        )

    assert error.value.category is ProviderErrorCategory.MALFORMED_RESPONSE

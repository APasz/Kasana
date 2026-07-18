"""Provider-neutral metadata contracts exchanged between Kourier and Katalog."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field


class ProviderMediaKind(StrEnum):
    MOVIE = "movie"
    SERIES = "series"
    SEASON = "season"
    EPISODE = "episode"


class ArtworkKind(StrEnum):
    POSTER = "poster"
    BACKDROP = "backdrop"
    STILL = "still"


class ProviderCapability(StrEnum):
    SEARCH_MOVIES = "search_movies"
    SEARCH_SERIES = "search_series"
    GET_MOVIE = "get_movie"
    GET_SERIES = "get_series"
    GET_SEASON = "get_season"
    GET_EPISODE = "get_episode"
    GET_ARTWORK = "get_artwork"


class ProviderErrorCategory(StrEnum):
    AUTHENTICATION = "authentication"
    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited"
    TRANSIENT = "transient"
    TIMEOUT = "timeout"
    MALFORMED_RESPONSE = "malformed_response"
    REQUEST_FAILED = "request_failed"
    UNSUPPORTED_OPERATION = "unsupported_operation"


class ProviderReference(BaseModel):
    """Stable provider name and opaque raw identifier for a future refresh."""

    model_config = ConfigDict(frozen=True)

    provider: str = Field(min_length=1, max_length=100)
    raw_id: str = Field(min_length=1, max_length=200)


class SearchQuery(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=500)
    year: int | None = Field(default=None, ge=1888, le=9999)
    include_adult: bool = False


class ExternalIdentifier(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    namespace: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=500)


class ArtworkReference(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str = Field(min_length=1, max_length=100)
    kind: ArtworkKind
    raw_path: str = Field(min_length=1, max_length=500)
    source_url: AnyHttpUrl | None = None
    language: str | None = Field(default=None, max_length=32)


class ArtworkContent(BaseModel):
    model_config = ConfigDict(frozen=True)

    reference: ArtworkReference
    content: bytes
    media_type: str | None = None


class ArtworkDownload(BaseModel):
    """Metadata returned after a provider streams artwork to a caller-owned file."""

    model_config = ConfigDict(frozen=True)

    content_type: str | None = None
    size_bytes: int = Field(ge=0)


class Country(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    code: str = Field(min_length=2, max_length=3)
    name: str | None = Field(default=None, max_length=200)


class SearchResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    reference: ProviderReference
    media_kind: ProviderMediaKind
    title: str = Field(min_length=1, max_length=500)
    original_title: str | None = Field(default=None, max_length=500)
    translated_title: str | None = Field(default=None, max_length=500)
    overview: str | None = None
    release_date: date | None = None
    poster: ArtworkReference | None = None
    backdrop: ArtworkReference | None = None
    original_language: str | None = Field(default=None, max_length=32)


class _TitleDetails(BaseModel):
    model_config = ConfigDict(frozen=True)

    reference: ProviderReference
    title: str = Field(min_length=1, max_length=500)
    original_title: str | None = Field(default=None, max_length=500)
    translated_title: str | None = Field(default=None, max_length=500)
    overview: str | None = None
    release_date: date | None = None
    poster: ArtworkReference | None = None
    backdrop: ArtworkReference | None = None
    genres: tuple[str, ...] = ()
    original_language: str | None = Field(default=None, max_length=32)
    countries: tuple[Country, ...] = ()
    external_ids: tuple[ExternalIdentifier, ...] = ()


class MovieDetails(_TitleDetails):
    media_kind: ProviderMediaKind = ProviderMediaKind.MOVIE
    runtime_minutes: int | None = Field(default=None, ge=0)


class SeriesDetails(_TitleDetails):
    media_kind: ProviderMediaKind = ProviderMediaKind.SERIES
    season_count: int | None = Field(default=None, ge=0)
    episode_count: int | None = Field(default=None, ge=0)


class EpisodeDetails(BaseModel):
    model_config = ConfigDict(frozen=True)

    reference: ProviderReference
    series_reference: ProviderReference
    season_number: int = Field(ge=0)
    episode_number: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=500)
    original_title: str | None = Field(default=None, max_length=500)
    translated_title: str | None = Field(default=None, max_length=500)
    overview: str | None = None
    air_date: date | None = None
    still: ArtworkReference | None = None
    runtime_minutes: int | None = Field(default=None, ge=0)
    external_ids: tuple[ExternalIdentifier, ...] = ()


class SeasonDetails(BaseModel):
    model_config = ConfigDict(frozen=True)

    reference: ProviderReference
    series_reference: ProviderReference
    season_number: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=500)
    overview: str | None = None
    air_date: date | None = None
    poster: ArtworkReference | None = None
    episodes: tuple[EpisodeDetails, ...] = ()
    external_ids: tuple[ExternalIdentifier, ...] = ()

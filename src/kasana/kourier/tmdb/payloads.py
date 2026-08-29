"""Validated shapes returned by TMDB's JSON endpoints."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from pydantic import AnyHttpUrl, BaseModel, BeforeValidator, ConfigDict, Field, field_validator


def empty_string_to_none(value: object) -> object:
    return None if isinstance(value, str) and not value.strip() else value


type OptionalDate = Annotated[date | None, BeforeValidator(empty_string_to_none)]
type CountryCode = Annotated[str, Field(min_length=2, max_length=3)]


class TMDBGenre(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None


class TMDBCountry(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    code: str = Field(alias="iso_3166_1", min_length=2, max_length=3)
    name: str | None = None


class TMDBExternalIDs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    imdb_id: str | None = None
    wikidata_id: str | None = None
    tvdb_id: int | None = None
    facebook_id: str | None = None
    instagram_id: str | None = None
    twitter_id: str | None = None


class TMDBMovieSearchEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    title: str = Field(min_length=1)
    original_title: str | None = None
    overview: str | None = None
    release_date: OptionalDate = None
    poster_path: str | None = None
    backdrop_path: str | None = None
    original_language: str | None = None


class TMDBSeriesSearchEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    name: str = Field(min_length=1)
    original_name: str | None = None
    overview: str | None = None
    first_air_date: OptionalDate = None
    poster_path: str | None = None
    backdrop_path: str | None = None
    original_language: str | None = None


class TMDBMovieSearchPage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    results: tuple[TMDBMovieSearchEntry, ...]


class TMDBSeriesSearchPage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    results: tuple[TMDBSeriesSearchEntry, ...]


class TMDBImagePayload(BaseModel):
    """One image variant returned by TMDB's title artwork endpoints."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    file_path: str = Field(min_length=1)
    language: str | None = Field(default=None, alias="iso_639_1", max_length=32)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    vote_average: float = Field(ge=0, le=10)
    vote_count: int = Field(ge=0)

    @field_validator("file_path")
    @classmethod
    def file_path_is_not_blank(cls, value: str) -> str:
        """Reject artwork records that cannot form a valid source URL."""

        value = value.strip()
        if not value:
            raise ValueError("TMDB image paths must not be blank.")
        return value


class TMDBImagesPayload(BaseModel):
    """Poster variants associated with one movie or series."""

    model_config = ConfigDict(extra="ignore")

    posters: tuple[TMDBImagePayload, ...] = ()


class TMDBImageConfiguration(BaseModel):
    """The image URL components returned by TMDB's configuration endpoint."""

    model_config = ConfigDict(extra="ignore")

    secure_base_url: AnyHttpUrl
    poster_sizes: tuple[str, ...]
    backdrop_sizes: tuple[str, ...]
    still_sizes: tuple[str, ...]

    @field_validator("poster_sizes", "backdrop_sizes", "still_sizes")
    @classmethod
    def image_sizes_are_valid(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values:
            raise ValueError("TMDB image size lists must not be empty.")
        for value in values:
            if value == "original":
                continue
            width = value.removeprefix("w")
            if width == value or not width.isdecimal() or int(width) < 1:
                raise ValueError("TMDB image sizes must be widths or 'original'.")
        return values


class TMDBConfigurationPayload(BaseModel):
    """The subset of TMDB's general configuration used to build image URLs."""

    model_config = ConfigDict(extra="ignore")

    images: TMDBImageConfiguration


class TMDBMoviePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    title: str = Field(min_length=1)
    original_title: str | None = None
    overview: str | None = None
    release_date: OptionalDate = None
    poster_path: str | None = None
    backdrop_path: str | None = None
    genres: tuple[TMDBGenre, ...] = ()
    original_language: str | None = None
    production_countries: tuple[TMDBCountry, ...] = ()
    external_ids: TMDBExternalIDs | None = None
    runtime: int | None = Field(default=None, ge=0)


class TMDBSeriesPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    name: str = Field(min_length=1)
    original_name: str | None = None
    overview: str | None = None
    first_air_date: OptionalDate = None
    poster_path: str | None = None
    backdrop_path: str | None = None
    genres: tuple[TMDBGenre, ...] = ()
    original_language: str | None = None
    production_countries: tuple[TMDBCountry, ...] = ()
    origin_country: tuple[CountryCode, ...] = ()
    external_ids: TMDBExternalIDs | None = None
    number_of_seasons: int | None = Field(default=None, ge=0)
    number_of_episodes: int | None = Field(default=None, ge=0)


class TMDBEpisodePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    name: str = Field(min_length=1)
    overview: str | None = None
    air_date: OptionalDate = None
    season_number: int = Field(ge=0)
    episode_number: int = Field(ge=0)
    still_path: str | None = None
    runtime: int | None = Field(default=None, ge=0)
    external_ids: TMDBExternalIDs | None = None


class TMDBSeasonPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    name: str = Field(min_length=1)
    overview: str | None = None
    air_date: OptionalDate = None
    season_number: int = Field(ge=0)
    poster_path: str | None = None
    episodes: tuple[TMDBEpisodePayload, ...] = ()
    external_ids: TMDBExternalIDs | None = None

"""Validated shapes returned by TMDB's JSON endpoints."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


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

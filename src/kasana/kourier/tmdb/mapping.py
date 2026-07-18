"""Map validated TMDB payload models to provider-neutral contracts."""

from __future__ import annotations

from pydantic import AnyHttpUrl
from yarl import URL

from kasana.kourier.tmdb.payloads import (
    CountryCode,
    TMDBCountry,
    TMDBEpisodePayload,
    TMDBExternalIDs,
    TMDBGenre,
    TMDBMovieSearchEntry,
    TMDBSeriesSearchEntry,
)
from kasana.kourier.tmdb.retry import TMDB_PROVIDER
from kasana.shared.metadata import (
    ArtworkKind,
    ArtworkReference,
    Country,
    EpisodeDetails,
    ExternalIdentifier,
    ProviderMediaKind,
    ProviderReference,
    SearchResult,
)


def reference(raw_id: int | str) -> ProviderReference:
    return ProviderReference(provider=TMDB_PROVIDER, raw_id=str(raw_id))


def artwork(
    path: str | None, kind: ArtworkKind, image_base_url: AnyHttpUrl
) -> ArtworkReference | None:
    if path is None or not path.strip():
        return None
    return ArtworkReference(
        provider=TMDB_PROVIDER,
        kind=kind,
        raw_path=path,
        source_url=AnyHttpUrl(str(URL(str(image_base_url).rstrip("/")) / path.lstrip("/"))),
    )


def movie_search_result(entry: TMDBMovieSearchEntry, image_base_url: AnyHttpUrl) -> SearchResult:
    return SearchResult(
        reference=reference(entry.id),
        media_kind=ProviderMediaKind.MOVIE,
        title=entry.title,
        original_title=entry.original_title,
        translated_title=entry.title,
        overview=entry.overview,
        release_date=entry.release_date,
        poster=artwork(entry.poster_path, ArtworkKind.POSTER, image_base_url),
        backdrop=artwork(entry.backdrop_path, ArtworkKind.BACKDROP, image_base_url),
        original_language=entry.original_language,
    )


def series_search_result(entry: TMDBSeriesSearchEntry, image_base_url: AnyHttpUrl) -> SearchResult:
    return SearchResult(
        reference=reference(entry.id),
        media_kind=ProviderMediaKind.SERIES,
        title=entry.name,
        original_title=entry.original_name,
        translated_title=entry.name,
        overview=entry.overview,
        release_date=entry.first_air_date,
        poster=artwork(entry.poster_path, ArtworkKind.POSTER, image_base_url),
        backdrop=artwork(entry.backdrop_path, ArtworkKind.BACKDROP, image_base_url),
        original_language=entry.original_language,
    )


def episode_details(
    episode: TMDBEpisodePayload,
    series_reference: ProviderReference,
    image_base_url: AnyHttpUrl,
) -> EpisodeDetails:
    return EpisodeDetails(
        reference=reference(episode.id),
        series_reference=series_reference,
        season_number=episode.season_number,
        episode_number=episode.episode_number,
        title=episode.name,
        translated_title=episode.name,
        overview=episode.overview,
        air_date=episode.air_date,
        still=artwork(episode.still_path, ArtworkKind.STILL, image_base_url),
        runtime_minutes=episode.runtime,
        external_ids=external_ids(episode.id, episode.external_ids),
    )


def genres(values: tuple[TMDBGenre, ...]) -> tuple[str, ...]:
    return tuple(value.name for value in values if value.name is not None and value.name.strip())


def countries(
    values: tuple[TMDBCountry, ...], origin_codes: tuple[CountryCode, ...] = ()
) -> tuple[Country, ...]:
    known_codes = {country.code for country in values}
    result = [Country(code=country.code, name=country.name) for country in values]
    result.extend(Country(code=code) for code in origin_codes if code not in known_codes)
    return tuple(result)


def external_ids(
    raw_id: int | str, values: TMDBExternalIDs | None
) -> tuple[ExternalIdentifier, ...]:
    identifiers = [ExternalIdentifier(namespace=TMDB_PROVIDER, value=str(raw_id))]
    if values is None:
        return tuple(identifiers)
    for namespace, value in (
        ("imdb", values.imdb_id),
        ("wikidata", values.wikidata_id),
        ("tvdb", str(values.tvdb_id) if values.tvdb_id is not None else None),
        ("facebook", values.facebook_id),
        ("instagram", values.instagram_id),
        ("twitter", values.twitter_id),
    ):
        if value is not None and value.strip():
            identifiers.append(ExternalIdentifier(namespace=namespace, value=value))
    return tuple(identifiers)

"""Map validated TMDB payload models to provider-neutral contracts."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import AnyHttpUrl
from yarl import URL

from kasana.kourier.tmdb.constants import TMDB_PROVIDER
from kasana.kourier.tmdb.payloads import (
    CountryCode,
    TMDBCountry,
    TMDBEpisodePayload,
    TMDBExternalIDs,
    TMDBGenre,
    TMDBImageConfiguration,
    TMDBImagePayload,
    TMDBMovieSearchEntry,
    TMDBSeriesSearchEntry,
)
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


@dataclass(frozen=True)
class TMDBImageUrls:
    """TMDB's configured image bases for Kasana's supported artwork kinds."""

    poster: AnyHttpUrl
    backdrop: AnyHttpUrl
    still: AnyHttpUrl

    def url(self, kind: ArtworkKind, path: str) -> AnyHttpUrl:
        if kind is ArtworkKind.POSTER:
            base_url = self.poster
        elif kind is ArtworkKind.BACKDROP:
            base_url = self.backdrop
        elif kind is ArtworkKind.STILL:
            base_url = self.still
        else:  # pragma: no cover - ArtworkKind is exhaustive.
            raise ValueError(f"Unsupported TMDB artwork kind: {kind!r}.")
        return AnyHttpUrl(str(URL(str(base_url).rstrip("/")) / path.lstrip("/")))


def configured_image_urls(
    configuration: TMDBImageConfiguration,
    *,
    target_width: int,
    use_original_images: bool,
) -> TMDBImageUrls:
    """Choose the largest rendition at or below the target, else the smallest."""

    if target_width < 1:
        raise ValueError("TMDB image target width must be positive.")

    return TMDBImageUrls(
        poster=_image_base_url(
            configuration.secure_base_url,
            _image_size(configuration.poster_sizes, target_width, use_original_images),
        ),
        backdrop=_image_base_url(
            configuration.secure_base_url,
            _image_size(configuration.backdrop_sizes, target_width, use_original_images),
        ),
        still=_image_base_url(
            configuration.secure_base_url,
            _image_size(configuration.still_sizes, target_width, use_original_images),
        ),
    )


def _image_base_url(base_url: AnyHttpUrl, size: str) -> AnyHttpUrl:
    return AnyHttpUrl(str(URL(str(base_url).rstrip("/")) / size))


def _image_size(sizes: tuple[str, ...], target_width: int, use_original_images: bool) -> str:
    if use_original_images and "original" in sizes:
        return "original"
    candidates = sorted(int(size[1:]) for size in sizes if size != "original")
    if not candidates:
        if "original" in sizes:
            return "original"
        raise ValueError("TMDB returned no usable image sizes.")
    selected = max((size for size in candidates if size <= target_width), default=candidates[0])
    return f"w{selected}"


def artwork(
    path: str | None, kind: ArtworkKind, image_urls: TMDBImageUrls | None
) -> ArtworkReference | None:
    if path is None or not path.strip():
        return None
    if image_urls is None:
        raise ValueError("TMDB image URLs are required for an artwork path.")
    return ArtworkReference(
        provider=TMDB_PROVIDER,
        kind=kind,
        raw_path=path,
        source_url=image_urls.url(kind, path),
    )


def poster_artwork(
    image: TMDBImagePayload, image_urls: TMDBImageUrls, *, is_primary: bool = False
) -> ArtworkReference:
    """Map one TMDB poster variant while retaining useful picker details."""

    return ArtworkReference(
        provider=TMDB_PROVIDER,
        kind=ArtworkKind.POSTER,
        raw_path=image.file_path,
        source_url=image_urls.url(ArtworkKind.POSTER, image.file_path),
        language=image.language,
        width=image.width,
        height=image.height,
        vote_average=image.vote_average,
        vote_count=image.vote_count,
        is_primary=is_primary,
    )


def movie_search_result(
    entry: TMDBMovieSearchEntry, image_urls: TMDBImageUrls | None
) -> SearchResult:
    return SearchResult(
        reference=reference(entry.id),
        media_kind=ProviderMediaKind.MOVIE,
        title=entry.title,
        original_title=entry.original_title,
        translated_title=entry.title,
        overview=entry.overview,
        release_date=entry.release_date,
        poster=artwork(entry.poster_path, ArtworkKind.POSTER, image_urls),
        backdrop=artwork(entry.backdrop_path, ArtworkKind.BACKDROP, image_urls),
        original_language=entry.original_language,
    )


def series_search_result(
    entry: TMDBSeriesSearchEntry, image_urls: TMDBImageUrls | None
) -> SearchResult:
    return SearchResult(
        reference=reference(entry.id),
        media_kind=ProviderMediaKind.SERIES,
        title=entry.name,
        original_title=entry.original_name,
        translated_title=entry.name,
        overview=entry.overview,
        release_date=entry.first_air_date,
        poster=artwork(entry.poster_path, ArtworkKind.POSTER, image_urls),
        backdrop=artwork(entry.backdrop_path, ArtworkKind.BACKDROP, image_urls),
        original_language=entry.original_language,
    )


def episode_details(
    episode: TMDBEpisodePayload,
    series_reference: ProviderReference,
    image_urls: TMDBImageUrls | None,
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
        still=artwork(episode.still_path, ArtworkKind.STILL, image_urls),
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

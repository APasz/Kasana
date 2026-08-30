"""Conservative path parsing for the library layouts Katalog currently supports."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from re import Match, Pattern

from kasana.katalog.numerals import NUMERAL_TOKEN_PATTERN, parse_numeral


class LibraryLayout(StrEnum):
    MOVIES = "movies"
    TV_SHOWS = "tv_shows"
    ANIME_SHOWS = "anime_shows"
    ANIME_FILM = "anime_film"
    ANIME = "anime"
    UNKNOWN = "unknown"


class ParsedMediaKind(StrEnum):
    MOVIE = "movie"
    EPISODE = "episode"
    SPECIAL = "special"
    EXTRA = "extra"


@dataclass(frozen=True)
class EpisodeIdentifier:
    """A season and episode number parsed from a filename."""

    season_number: int
    episode_number: int


@dataclass(frozen=True)
class EpisodeRange:
    """One episode or an ordered, combined-episode range in a media filename."""

    start: EpisodeIdentifier
    end: EpisodeIdentifier | None = None

    @property
    def is_combined(self) -> bool:
        return self.end is not None

    @property
    def is_forward(self) -> bool:
        return self.end is None or (self.end.season_number, self.end.episode_number) > (
            self.start.season_number,
            self.start.episode_number,
        )


@dataclass(frozen=True)
class ParsedMedia:
    kind: ParsedMediaKind
    title: str
    release_year: int | None = None
    series_title: str | None = None
    season_number: int | None = None
    episode_number: int | None = None
    episode_end_season_number: int | None = None
    episode_end_number: int | None = None
    parent_movie_title: str | None = None
    parent_series_title: str | None = None
    is_directory_movie: bool = False


@dataclass(frozen=True)
class ParseFailure:
    message: str


_DECADE_PATTERN: Pattern[str] = re.compile(r"^(?:(?:18|19|20)\d{2}'?s|\d{2}'s)$", re.IGNORECASE)
_YEAR_SUFFIX_PATTERN: Pattern[str] = re.compile(r"\s*\((?P<year>(?:18|19|20)\d{2})\)$")
_SEASON_PATTERN: Pattern[str] = re.compile(
    rf"^(?P<label>season|volume|シーズン|시즌)[. _-]*(?P<number>{NUMERAL_TOKEN_PATTERN})$",
    re.IGNORECASE,
)
_ORDINAL_SEASON_PATTERN: Pattern[str] = re.compile(
    rf"^第\s*(?P<number>{NUMERAL_TOKEN_PATTERN})\s*(?:季|期|기)$"
)
_SEASON_EPISODE_PATTERN: Pattern[str] = re.compile(
    rf"(?:^|[. _-])(?:season|s)(?P<season>{NUMERAL_TOKEN_PATTERN})[. _-]*"
    rf"(?:episode|ep|e)(?P<episode>{NUMERAL_TOKEN_PATTERN})(?:$|[. _-])",
    re.IGNORECASE,
)
_SEASON_EPISODE_RANGE_PATTERN: Pattern[str] = re.compile(
    rf"(?:^|[. _-])(?:season|s)(?P<season>{NUMERAL_TOKEN_PATTERN})[. _-]*"
    rf"(?:episode|ep|e)(?P<episode>{NUMERAL_TOKEN_PATTERN})"
    r"(?:[. _-]*(?:-|&|and)[. _-]*|[. _-]*)"
    rf"(?:(?:season|s)(?P<end_season>{NUMERAL_TOKEN_PATTERN})[. _-]*)?"
    rf"(?:episode|ep|e)(?P<end_episode>{NUMERAL_TOKEN_PATTERN})"
    r"(?:$|[. _-])",
    re.IGNORECASE,
)
_ALTERNATE_SEASON_EPISODE_PATTERN: Pattern[str] = re.compile(
    rf"(?:\[(?P<bracket_season>{NUMERAL_TOKEN_PATTERN})[xX](?P<bracket_episode>{NUMERAL_TOKEN_PATTERN})\]"
    rf"|\((?P<parenthetical_season>{NUMERAL_TOKEN_PATTERN})[xX](?P<parenthetical_episode>{NUMERAL_TOKEN_PATTERN})\))"
)
_EPISODE_PATTERN: Pattern[str] = re.compile(
    rf"(?:^|[. _-])(?:episode|ep|e)(?P<episode>{NUMERAL_TOKEN_PATTERN})(?:$|[. _-])",
    re.IGNORECASE,
)
_EAST_ASIAN_EPISODE_PATTERN: Pattern[str] = re.compile(
    rf"(?:第\s*(?P<han_episode>{NUMERAL_TOKEN_PATTERN})\s*(?:話|话|集|回)"
    rf"|제\s*(?P<hangul_episode>{NUMERAL_TOKEN_PATTERN})\s*(?:화|회))"
)
_SEASON_EPISODE_MARKER = (
    rf"(?:^|[. _-])(?:season|s){NUMERAL_TOKEN_PATTERN}"
    rf"[. _-]*(?:episode|ep|e){NUMERAL_TOKEN_PATTERN}"
)
_EPISODE_RANGE_SUFFIX = (
    rf"(?:[. _-]*(?:-|&|and)[. _-]*|[. _-]*)"
    rf"(?:(?:season|s){NUMERAL_TOKEN_PATTERN}[. _-]*)?"
    rf"(?:episode|ep|e){NUMERAL_TOKEN_PATTERN}"
)
_EPISODE_MARKER_PATTERN: Pattern[str] = re.compile(
    _SEASON_EPISODE_MARKER + rf"(?:{_EPISODE_RANGE_SUFFIX})?"
    r"(?:$|[. _-])"
    rf"|(?:^|[. _-])(?:episode|ep|e){NUMERAL_TOKEN_PATTERN}(?:$|[. _-])"
    rf"|\[{NUMERAL_TOKEN_PATTERN}[xX]{NUMERAL_TOKEN_PATTERN}\]"
    rf"|\({NUMERAL_TOKEN_PATTERN}[xX]{NUMERAL_TOKEN_PATTERN}\)"
    rf"|第\s*{NUMERAL_TOKEN_PATTERN}\s*(?:話|话|集|回)"
    rf"|제\s*{NUMERAL_TOKEN_PATTERN}\s*(?:화|회)",
    re.IGNORECASE,
)
_RECOGNISED_EXTRA_PATTERN: Pattern[str] = re.compile(
    r"(?:^|[. _-])(?:extra(?:s)?|bonus|trailer|featurette|sample|"
    r"behind[. _-]*the[. _-]*scenes|deleted[. _-]*scenes?)(?:$|[. _-])",
    re.IGNORECASE,
)
_EXTRA_DIRECTORY_NAMES = frozenset({"extra", "extras"})


def infer_library_layout(root_path: Path) -> LibraryLayout:
    match root_path.name.casefold():
        case "movies":
            return LibraryLayout.MOVIES
        case "tvshows":
            return LibraryLayout.TV_SHOWS
        case "animeshows":
            return LibraryLayout.ANIME_SHOWS
        case "animefilm":
            return LibraryLayout.ANIME_FILM
        case "anime":
            return LibraryLayout.ANIME
        case _:
            return LibraryLayout.UNKNOWN


def parse_season_number(directory_name: str, *, allow_volume: bool) -> int | None:
    match: Match[str] | None = _SEASON_PATTERN.fullmatch(directory_name.strip())
    if match is not None:
        if not allow_volume and match.group("label").casefold() == "volume":
            return None
        return _parse_season_numeral(match.group("number"))
    ordinal_match = _ORDINAL_SEASON_PATTERN.fullmatch(directory_name.strip())
    return (
        _parse_season_numeral(ordinal_match.group("number")) if ordinal_match is not None else None
    )


def _parse_season_numeral(value: str) -> int | None:
    """Parse the bounded season component of a media identifier."""

    return parse_numeral(value, maximum=99)


def _parse_episode_numeral(value: str) -> int | None:
    """Parse the bounded episode component of a media identifier."""

    return parse_numeral(value, maximum=999)


def parse_episode_numbers(
    filename_stem: str, *, season_from_directory: int | None
) -> tuple[int, int] | None:
    episode_range = parse_episode_range(filename_stem, season_from_directory=season_from_directory)
    if episode_range is None:
        return None
    return episode_range.start.season_number, episode_range.start.episode_number


def parse_episode_range(
    filename_stem: str, *, season_from_directory: int | None
) -> EpisodeRange | None:
    season_episode_range: Match[str] | None = _SEASON_EPISODE_RANGE_PATTERN.search(filename_stem)
    if season_episode_range is not None:
        season_number = _parse_season_numeral(season_episode_range.group("season"))
        episode_number = _parse_episode_numeral(season_episode_range.group("episode"))
        end_season_number = _parse_season_numeral(
            season_episode_range.group("end_season") or season_episode_range.group("season")
        )
        end_episode_number = _parse_episode_numeral(season_episode_range.group("end_episode"))
        if (
            season_number is None
            or episode_number is None
            or end_season_number is None
            or end_episode_number is None
        ):
            return None
        start = EpisodeIdentifier(
            season_number=season_number,
            episode_number=episode_number,
        )
        end = EpisodeIdentifier(
            season_number=end_season_number,
            episode_number=end_episode_number,
        )
        return EpisodeRange(start=start, end=end)
    alternate_season_episode: Match[str] | None = _ALTERNATE_SEASON_EPISODE_PATTERN.search(
        filename_stem
    )
    if alternate_season_episode is not None:
        marker_season: str | None = alternate_season_episode.group(
            "bracket_season"
        ) or alternate_season_episode.group("parenthetical_season")
        marker_episode: str | None = alternate_season_episode.group(
            "bracket_episode"
        ) or alternate_season_episode.group("parenthetical_episode")
        assert marker_season is not None
        assert marker_episode is not None
        season_number = _parse_season_numeral(marker_season)
        episode_number = _parse_episode_numeral(marker_episode)
        if season_number is None or episode_number is None:
            return None
        return EpisodeRange(
            start=EpisodeIdentifier(season_number=season_number, episode_number=episode_number)
        )
    season_episode: Match[str] | None = _SEASON_EPISODE_PATTERN.search(filename_stem)
    if season_episode is not None:
        season_number = _parse_season_numeral(season_episode.group("season"))
        episode_number = _parse_episode_numeral(season_episode.group("episode"))
        if season_number is None or episode_number is None:
            return None
        return EpisodeRange(
            start=EpisodeIdentifier(
                season_number=season_number,
                episode_number=episode_number,
            )
        )
    if season_from_directory is None:
        return None
    episode: Match[str] | None = _EPISODE_PATTERN.search(filename_stem)
    if episode is not None:
        episode_number = _parse_episode_numeral(episode.group("episode"))
        if episode_number is None:
            return None
        return EpisodeRange(
            start=EpisodeIdentifier(
                season_number=season_from_directory, episode_number=episode_number
            )
        )
    east_asian_episode = _EAST_ASIAN_EPISODE_PATTERN.search(filename_stem)
    if east_asian_episode is None:
        return None
    numeral = east_asian_episode.group("han_episode") or east_asian_episode.group("hangul_episode")
    assert numeral is not None
    episode_number = _parse_episode_numeral(numeral)
    return (
        EpisodeRange(
            start=EpisodeIdentifier(
                season_number=season_from_directory, episode_number=episode_number
            )
        )
        if episode_number is not None
        else None
    )


def parse_media_path(
    root_path: Path, layout: LibraryLayout, path: Path
) -> ParsedMedia | ParseFailure:
    relative_parts: tuple[str, ...] = path.relative_to(root_path).parts
    directories: tuple[str, ...] = relative_parts[:-1]
    filename_stem: str = path.stem
    match layout:
        case LibraryLayout.MOVIES:
            return _parse_movie_path(directories, filename_stem, has_decade_directory=True)
        case LibraryLayout.ANIME_FILM:
            return _parse_movie_path(directories, filename_stem, has_decade_directory=False)
        case LibraryLayout.ANIME:
            return _parse_anime_path(directories, filename_stem)
        case LibraryLayout.TV_SHOWS:
            return _parse_episode_path(directories, filename_stem, allow_volume=False)
        case LibraryLayout.ANIME_SHOWS:
            return _parse_episode_path(directories, filename_stem, allow_volume=True)
        case LibraryLayout.UNKNOWN:
            return ParseFailure("The library root name does not identify a supported layout.")


def _parse_movie_path(
    directories: tuple[str, ...], filename_stem: str, *, has_decade_directory: bool
) -> ParsedMedia | ParseFailure:
    effective_directories: tuple[str, ...] = directories
    if (
        has_decade_directory
        and effective_directories
        and is_decade_directory(effective_directories[0])
    ):
        effective_directories = effective_directories[1:]
    if not effective_directories:
        title, release_year = _movie_title_and_year(filename_stem)
        return ParsedMedia(kind=ParsedMediaKind.MOVIE, title=title, release_year=release_year)
    if len(effective_directories) == 1:
        title, release_year = _movie_title_and_year(effective_directories[0])
        if _is_recognised_extra(filename_stem):
            return ParsedMedia(
                kind=ParsedMediaKind.EXTRA,
                title=filename_stem,
                parent_movie_title=title,
            )
        return ParsedMedia(
            kind=ParsedMediaKind.MOVIE,
            title=title,
            release_year=release_year,
            is_directory_movie=True,
        )
    if len(effective_directories) == 2 and effective_directories[1].casefold() == "extras":
        parent_movie_title, _ = _movie_title_and_year(effective_directories[0])
        return ParsedMedia(
            kind=ParsedMediaKind.EXTRA,
            title=filename_stem,
            parent_movie_title=parent_movie_title,
        )
    return ParseFailure(
        "Movie files must be direct children of a title directory or its extras directory."
    )


def _parse_anime_path(
    directories: tuple[str, ...], filename_stem: str
) -> ParsedMedia | ParseFailure:
    if not directories:
        return ParseFailure("Anime files must be below the Shows or Films organisational folder.")
    category, *remainder = directories
    match category.casefold():
        case "shows":
            return _parse_episode_path(tuple(remainder), filename_stem, allow_volume=True)
        case "films":
            return _parse_movie_path(tuple(remainder), filename_stem, has_decade_directory=False)
        case _:
            return ParseFailure(
                "Anime files must be below the Shows or Films organisational folder."
            )


def _parse_episode_path(
    directories: tuple[str, ...], filename_stem: str, *, allow_volume: bool
) -> ParsedMedia | ParseFailure:
    if any(directory.casefold() in _EXTRA_DIRECTORY_NAMES for directory in directories):
        if not directories or directories[0].casefold() in _EXTRA_DIRECTORY_NAMES:
            return ParseFailure("Series extras must be below a show title directory.")
        return ParsedMedia(
            kind=ParsedMediaKind.EXTRA,
            title=filename_stem,
            parent_series_title=directories[0],
        )
    if len(directories) != 2:
        return ParseFailure("Episode files must be under <show title>/<Season or Volume number>/.")
    series_title, season_directory = directories
    season_number = parse_season_number(season_directory, allow_volume=allow_volume)
    if season_number is None:
        return ParseFailure("The episode directory does not establish a season or volume number.")
    if season_number == 0:
        return ParsedMedia(
            kind=ParsedMediaKind.SPECIAL,
            title=_special_title(filename_stem, series_title=series_title),
            series_title=series_title,
        )
    episode_range = parse_episode_range(filename_stem, season_from_directory=season_number)
    if episode_range is None:
        return ParseFailure("The episode filename has no unambiguous episode identifier.")
    parsed_season = episode_range.start.season_number
    episode_number = episode_range.start.episode_number
    if parsed_season != season_number:
        return ParseFailure(
            "The filename season number conflicts with its containing season directory."
        )
    if not episode_range.is_forward:
        return ParseFailure("A combined episode filename must end after its starting episode.")
    title = _episode_title(
        filename_stem,
        series_title=series_title,
        season_number=season_number,
        episode_number=episode_number,
        episode_end_season_number=(
            episode_range.end.season_number if episode_range.end is not None else None
        ),
        episode_end_number=(
            episode_range.end.episode_number if episode_range.end is not None else None
        ),
    )
    return ParsedMedia(
        kind=ParsedMediaKind.EPISODE,
        title=title,
        series_title=series_title,
        season_number=season_number,
        episode_number=episode_number,
        episode_end_season_number=(
            episode_range.end.season_number if episode_range.end is not None else None
        ),
        episode_end_number=(
            episode_range.end.episode_number if episode_range.end is not None else None
        ),
    )


def is_decade_directory(directory_name: str) -> bool:
    """Recognise organisational decade folders without treating numeric titles as folders."""

    return _DECADE_PATTERN.fullmatch(directory_name) is not None


def has_episode_marker(filename_stem: str) -> bool:
    """Return whether a filename itself supplies an episode identifier."""

    return _EPISODE_MARKER_PATTERN.search(filename_stem) is not None


def _is_recognised_extra(filename_stem: str) -> bool:
    """Keep clear feature labels beneath their logical movie rather than promoting them."""

    return _RECOGNISED_EXTRA_PATTERN.search(filename_stem) is not None


def _movie_title_and_year(value: str) -> tuple[str, int | None]:
    match = _YEAR_SUFFIX_PATTERN.search(value)
    if match is None:
        return value, None
    title = value[: match.start()].strip()
    return (title, int(match.group("year"))) if title else (value, None)


def _episode_title(
    filename_stem: str,
    *,
    series_title: str,
    season_number: int,
    episode_number: int,
    episode_end_season_number: int | None,
    episode_end_number: int | None,
) -> str:
    stripped = _EPISODE_MARKER_PATTERN.sub(" ", filename_stem)
    normalised = _normalise_filename_title(stripped)
    title = _without_series_title_prefix(normalised, series_title)
    episode_label = f"S{season_number:02d}E{episode_number:02d}"
    if episode_end_season_number is not None and episode_end_number is not None:
        end_label = (
            f"S{episode_end_season_number:02d}E{episode_end_number:02d}"
            if episode_end_season_number != season_number
            else f"E{episode_end_number:02d}"
        )
        episode_label = f"{episode_label}-{end_label}"
        if title:
            return f"{episode_label} - {title}"
        return episode_label
    if title:
        return title
    return episode_label


def _special_title(filename_stem: str, *, series_title: str) -> str:
    stripped = _EPISODE_MARKER_PATTERN.sub(" ", filename_stem)
    normalised = _normalise_filename_title(stripped)
    if normalised and normalised.casefold() != series_title.casefold():
        return normalised
    return filename_stem


def _normalise_filename_title(value: str) -> str:
    return " ".join(value.replace(".", " ").replace("_", " ").split()).strip("- ")


def _without_series_title_prefix(title: str, series_title: str) -> str:
    """Remove a filename's repeated series prefix and its separators."""

    prefix_length = len(series_title)
    if title[:prefix_length].casefold() != series_title.casefold():
        return title
    remainder = title[prefix_length:]
    if remainder and remainder[0] not in {" ", "-"}:
        return title
    return remainder.strip("- ")

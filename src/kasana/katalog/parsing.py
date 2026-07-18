"""Conservative path parsing for the library layouts Katalog currently supports."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from re import Match, Pattern


class LibraryLayout(StrEnum):
    MOVIES = "movies"
    TV_SHOWS = "tv_shows"
    ANIME_SHOWS = "anime_shows"
    ANIME_FILM = "anime_film"
    UNKNOWN = "unknown"


class ParsedMediaKind(StrEnum):
    MOVIE = "movie"
    EPISODE = "episode"
    SPECIAL = "special"
    EXTRA = "extra"


@dataclass(frozen=True)
class ParsedMedia:
    kind: ParsedMediaKind
    title: str
    release_year: int | None = None
    series_title: str | None = None
    season_number: int | None = None
    episode_number: int | None = None
    parent_movie_title: str | None = None
    parent_series_title: str | None = None


@dataclass(frozen=True)
class ParseFailure:
    message: str


_DECADE_PATTERN: Pattern[str] = re.compile(
    r"^(?:(?:18|19|20)\d{2}s|(?:0\d|1\d|2\d)'s)$", re.IGNORECASE
)
_YEAR_SUFFIX_PATTERN: Pattern[str] = re.compile(r"\s*\((?P<year>(?:18|19|20)\d{2})\)$")
_SEASON_PATTERN: Pattern[str] = re.compile(
    r"^(?:season|volume)\s*(?P<number>\d{1,3})$", re.IGNORECASE
)
_SEASON_EPISODE_PATTERN: Pattern[str] = re.compile(
    r"(?:^|[. _-])s(?P<season>\d{1,2})[. _-]*e(?P<episode>\d{1,3})(?:$|[. _-])",
    re.IGNORECASE,
)
_ALTERNATE_SEASON_EPISODE_PATTERN: Pattern[str] = re.compile(
    r"(?:\[(?P<bracket_season>\d{1,2})[xX](?P<bracket_episode>\d{1,3})\]"
    r"|\((?P<parenthetical_season>\d{1,2})[xX](?P<parenthetical_episode>\d{1,3})\))"
)
_EPISODE_PATTERN: Pattern[str] = re.compile(
    r"(?:^|[. _-])e(?P<episode>\d{1,3})(?:$|[. _-])", re.IGNORECASE
)
_EPISODE_MARKER_PATTERN: Pattern[str] = re.compile(
    r"(?:^|[. _-])s\d{1,2}[. _-]*e\d{1,3}(?:$|[. _-])"
    r"|(?:^|[. _-])e\d{1,3}(?:$|[. _-])"
    r"|\[\d{1,2}[xX]\d{1,3}\]"
    r"|\(\d{1,2}[xX]\d{1,3}\)",
    re.IGNORECASE,
)


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
        case _:
            return LibraryLayout.UNKNOWN


def parse_season_number(directory_name: str, *, allow_volume: bool) -> int | None:
    match: Match[str] | None = _SEASON_PATTERN.fullmatch(directory_name.strip())
    if match is None:
        return None
    if not allow_volume and directory_name.casefold().startswith("volume"):
        return None
    return int(match.group("number"))


def parse_episode_numbers(
    filename_stem: str, *, season_from_directory: int | None
) -> tuple[int, int] | None:
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
        return int(marker_season), int(marker_episode)
    season_episode: Match[str] | None = _SEASON_EPISODE_PATTERN.search(filename_stem)
    if season_episode is not None:
        return int(season_episode.group("season")), int(season_episode.group("episode"))
    if season_from_directory is None:
        return None
    episode: Match[str] | None = _EPISODE_PATTERN.search(filename_stem)
    if episode is None:
        return None
    return season_from_directory, int(episode.group("episode"))


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
        and _is_decade_directory(effective_directories[0])
    ):
        effective_directories = effective_directories[1:]
    if not effective_directories:
        title, release_year = _movie_title_and_year(filename_stem)
        return ParsedMedia(kind=ParsedMediaKind.MOVIE, title=title, release_year=release_year)
    if len(effective_directories) == 1:
        title, release_year = _movie_title_and_year(effective_directories[0])
        return ParsedMedia(kind=ParsedMediaKind.MOVIE, title=title, release_year=release_year)
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


def _parse_episode_path(
    directories: tuple[str, ...], filename_stem: str, *, allow_volume: bool
) -> ParsedMedia | ParseFailure:
    if any(directory.casefold() == "extras" for directory in directories):
        if not directories or directories[0].casefold() == "extras":
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
    episode_numbers = parse_episode_numbers(filename_stem, season_from_directory=season_number)
    if episode_numbers is None:
        return ParseFailure("The episode filename has no unambiguous episode identifier.")
    parsed_season, episode_number = episode_numbers
    if parsed_season != season_number:
        return ParseFailure(
            "The filename season number conflicts with its containing season directory."
        )
    title = _episode_title(
        filename_stem,
        series_title=series_title,
        season_number=season_number,
        episode_number=episode_number,
    )
    return ParsedMedia(
        kind=ParsedMediaKind.EPISODE,
        title=title,
        series_title=series_title,
        season_number=season_number,
        episode_number=episode_number,
    )


def _is_decade_directory(directory_name: str) -> bool:
    return _DECADE_PATTERN.fullmatch(directory_name) is not None


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
) -> str:
    stripped = _EPISODE_MARKER_PATTERN.sub(" ", filename_stem)
    normalized = " ".join(stripped.replace(".", " ").replace("_", " ").split()).strip("- ")
    if normalized and normalized.casefold() != series_title.casefold():
        return normalized
    return f"S{season_number:02d}E{episode_number:02d}"


def _special_title(filename_stem: str, *, series_title: str) -> str:
    stripped = _EPISODE_MARKER_PATTERN.sub(" ", filename_stem)
    normalized = " ".join(stripped.replace(".", " ").replace("_", " ").split()).strip("- ")
    if normalized and normalized.casefold() != series_title.casefold():
        return normalized
    return filename_stem

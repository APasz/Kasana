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
    EXTRA = "extra"


@dataclass(frozen=True)
class ParsedMedia:
    kind: ParsedMediaKind
    title: str
    series_title: str | None = None
    season_number: int | None = None
    episode_number: int | None = None
    parent_movie_title: str | None = None


@dataclass(frozen=True)
class ParseFailure:
    message: str


_DECADE_PATTERN: Pattern[str] = re.compile(r"^(?:18|19|20)\d{2}s$", re.IGNORECASE)
_SEASON_PATTERN: Pattern[str] = re.compile(
    r"^(?:season|volume)\s*(?P<number>\d{1,3})$", re.IGNORECASE
)
_SEASON_EPISODE_PATTERN: Pattern[str] = re.compile(
    r"(?:^|[. _-])s(?P<season>\d{1,2})[. _-]*e(?P<episode>\d{1,3})(?:$|[. _-])",
    re.IGNORECASE,
)
_EPISODE_PATTERN: Pattern[str] = re.compile(
    r"(?:^|[. _-])e(?P<episode>\d{1,3})(?:$|[. _-])", re.IGNORECASE
)
_EPISODE_MARKER_PATTERN: Pattern[str] = re.compile(
    r"(?:^|[. _-])s\d{1,2}[. _-]*e\d{1,3}(?:$|[. _-])|(?:^|[. _-])e\d{1,3}(?:$|[. _-])",
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
        return ParsedMedia(kind=ParsedMediaKind.MOVIE, title=filename_stem)
    if len(effective_directories) == 1:
        return ParsedMedia(kind=ParsedMediaKind.MOVIE, title=effective_directories[0])
    if len(effective_directories) == 2 and effective_directories[1].casefold() == "extras":
        return ParsedMedia(
            kind=ParsedMediaKind.EXTRA,
            title=filename_stem,
            parent_movie_title=effective_directories[0],
        )
    return ParseFailure(
        "Movie files must be direct children of a title directory or its extras directory."
    )


def _parse_episode_path(
    directories: tuple[str, ...], filename_stem: str, *, allow_volume: bool
) -> ParsedMedia | ParseFailure:
    if len(directories) != 2:
        return ParseFailure("Episode files must be under <show title>/<Season or Volume number>/.")
    series_title, season_directory = directories
    season_number = parse_season_number(season_directory, allow_volume=allow_volume)
    if season_number is None:
        return ParseFailure("The episode directory does not establish a season or volume number.")
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

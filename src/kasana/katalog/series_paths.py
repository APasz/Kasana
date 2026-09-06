"""Resolve trusted series-directory aliases from current catalogue evidence."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
from pathlib import Path

from kasana.katalog.metadata.scoring import normalise_title
from kasana.katalog.models import AvailabilityState, MediaFile, Zaisan, ZaisanKind
from kasana.katalog.parsing import (
    LibraryLayout,
    ParsedMedia,
    ParsedMediaKind,
    ParseFailure,
    parse_media_path,
)


def series_title_identity(title: str) -> str:
    """Return the stable title identity used for series paths and aliases."""

    tokens = normalise_title(title).split()
    if tokens[:1] == ["the"]:
        tokens = tokens[1:]
    return "".join(tokens)


def accepted_series_path_aliases(
    *,
    root_path: Path,
    layout: LibraryLayout,
    items: Sequence[Zaisan],
    media_files: Iterable[MediaFile],
    matched_series_ids: Collection[int],
    current_media_paths: Mapping[int, Path] | None = None,
) -> dict[str, Zaisan]:
    """Map path titles to accepted series only when the evidence is unambiguous.

    A directory name can be an abbreviation or a release-style title rather than
    the canonical provider title.  It becomes an alias only when every known
    media record under that directory is already attached to one
    metadata-matched series.  The map is derived from current paths instead of
    being persisted, so a renamed or removed directory cannot leave a stale
    alias behind.  Callers applying a scan may provide the just-discovered paths
    for moved files.
    """

    if layout not in {
        LibraryLayout.TV_SHOWS,
        LibraryLayout.ANIME_SHOWS,
        LibraryLayout.ANIME,
    }:
        return {}
    matched_ids = frozenset(matched_series_ids)
    if not matched_ids:
        return {}
    items_by_id = {item.id: item for item in items}
    series_ids_by_identity: defaultdict[str, set[int]] = defaultdict(set)
    for item in items:
        if item.item_kind is ZaisanKind.SERIES and item.parent_id is None:
            series_ids_by_identity[series_title_identity(item.sort_title)].add(item.id)

    series_by_path_identity: defaultdict[str, dict[int, Zaisan]] = defaultdict(dict)
    for media_file in media_files:
        if media_file.availability is not AvailabilityState.AVAILABLE:
            continue
        series = _series_ancestor(media_file.library_item_id, items_by_id)
        if series is None:
            continue
        path = (
            Path(media_file.absolute_path)
            if current_media_paths is None
            else current_media_paths.get(media_file.id)
        )
        if path is None:
            continue
        title = _path_series_title(root_path, layout, path)
        if title is None:
            continue
        identity = series_title_identity(title)
        if identity:
            series_by_path_identity[identity][series.id] = series

    aliases: dict[str, Zaisan] = {}
    for identity, series_by_id in series_by_path_identity.items():
        if len(series_by_id) != 1:
            continue
        series = next(iter(series_by_id.values()))
        if series.id not in matched_ids:
            continue
        if series_ids_by_identity[identity] - {series.id}:
            continue
        aliases[identity] = series
    return aliases


def _series_ancestor(item_id: int, items_by_id: dict[int, Zaisan]) -> Zaisan | None:
    current = items_by_id.get(item_id)
    visited: set[int] = set()
    while current is not None:
        if current.id in visited:
            return None
        visited.add(current.id)
        if current.item_kind is ZaisanKind.SERIES and current.parent_id is None:
            return current
        if current.parent_id is None:
            return None
        current = items_by_id.get(current.parent_id)
    return None


def _path_series_title(root_path: Path, layout: LibraryLayout, path: Path) -> str | None:
    try:
        parsed = parse_media_path(root_path, layout, path)
    except ValueError:
        return None
    if isinstance(parsed, ParseFailure):
        return None
    return _parsed_series_title(parsed)


def _parsed_series_title(parsed: ParsedMedia) -> str | None:
    if parsed.kind in {ParsedMediaKind.EPISODE, ParsedMediaKind.SPECIAL}:
        return parsed.series_title
    if parsed.kind is ParsedMediaKind.EXTRA:
        return parsed.parent_series_title
    return None

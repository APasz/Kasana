"""Conservative structural auditing for catalogue records created before safe scanning."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from kasana.katalog.models import AuditCategory, Kura, MediaFile, Zaisan, ZaisanKind
from kasana.katalog.parsing import LibraryLayout, has_episode_marker, is_decade_directory
from kasana.katalog.scanning.discovery import AuditFinding

_ORGANISATIONAL_NAMES = frozenset(
    {
        "movies",
        "tvshows",
        "anime",
        "shows",
        "films",
        "extras",
        "subtitles",
    }
)
_SEASON_OR_VOLUME_PATTERN = re.compile(r"^(?:season|volume)\s*\d{1,3}$", re.IGNORECASE)
_TITLE_TOKEN_PATTERN = re.compile(r"[^a-z0-9]+")


def structural_findings(
    root: Kura,
    *,
    layout: LibraryLayout,
    items: Sequence[Zaisan],
    media_files: Sequence[MediaFile],
) -> tuple[AuditFinding, ...]:
    """Find repairable hierarchy mistakes without guessing at ambiguous identities.

    All messages begin with a stable machine-readable code.  The code is kept
    in the human-facing message because historic audit rows intentionally have
    no loosely typed details column and remain compatible with existing data.
    """

    items_by_id = {item.id: item for item in items}
    files_by_item: dict[int, list[MediaFile]] = defaultdict(list)
    for media_file in media_files:
        files_by_item[media_file.library_item_id].append(media_file)

    findings: list[AuditFinding] = []
    for item in items:
        owned_files = tuple(files_by_item[item.id])
        if item.item_kind is ZaisanKind.MOVIE:
            findings.extend(_movie_findings(root, layout, item, owned_files))
        if item.item_kind in _playable_kinds() and _is_organisational_item(item, owned_files):
            findings.append(
                _finding(
                    "organisational_folder_as_item",
                    root,
                    item,
                    f"Playable {item.item_kind.value} item {item.id} is named like an "
                    "organisational folder and its media path confirms that context.",
                )
            )
        findings.extend(_parent_findings(root, item, items_by_id))
        findings.extend(_media_hierarchy_findings(root, item, owned_files))

    findings.extend(_duplicate_series_findings(root, items))
    return tuple(findings)


def _movie_findings(
    root: Kura,
    layout: LibraryLayout,
    item: Zaisan,
    files: Sequence[MediaFile],
) -> tuple[AuditFinding, ...]:
    findings: list[AuditFinding] = []
    if (
        layout is LibraryLayout.MOVIES
        and is_decade_directory(item.title)
        and any(_path_has_component(file.absolute_path, item.title) for file in files)
    ):
        findings.append(
            _finding(
                "decade_folder_as_movie",
                root,
                item,
                f"Movie item {item.id} uses decade folder name {item.title!r}; its media path "
                "places the file below that organisational directory.",
            )
        )
    if len(files) > 1 and _has_unrelated_movie_files(item, files):
        findings.append(
            _finding(
                "multiple_unrelated_movie_media",
                root,
                item,
                f"Movie item {item.id} owns {len(files)} distinct main-media candidates; "
                "automatic merge or primary-file selection is unsafe.",
            )
        )
    return tuple(findings)


def _parent_findings(
    root: Kura, item: Zaisan, items_by_id: Mapping[int, Zaisan]
) -> tuple[AuditFinding, ...]:
    parent = items_by_id.get(item.parent_id) if item.parent_id is not None else None
    if item.item_kind is ZaisanKind.SEASON and (
        parent is None or parent.item_kind is not ZaisanKind.SERIES
    ):
        return (
            _finding(
                "season_without_series",
                root,
                item,
                f"Season item {item.id} is not parented by a series.",
            ),
        )
    if item.item_kind is ZaisanKind.EPISODE:
        series = (
            items_by_id.get(parent.parent_id)
            if parent is not None and parent.parent_id is not None
            else None
        )
        if (
            parent is None
            or parent.item_kind is not ZaisanKind.SEASON
            or (series is None or series.item_kind is not ZaisanKind.SERIES)
        ):
            return (
                _finding(
                    "episode_without_series",
                    root,
                    item,
                    f"Episode item {item.id} is not beneath a season owned by a series.",
                ),
            )
    if item.item_kind is ZaisanKind.EXTRA and parent is None:
        return (
            _finding(
                "top_level_extra",
                root,
                item,
                f"Extra item {item.id} has no movie, series, or episode parent.",
            ),
        )
    return ()


def _media_hierarchy_findings(
    root: Kura, item: Zaisan, files: Iterable[MediaFile]
) -> tuple[AuditFinding, ...]:
    findings: list[AuditFinding] = []
    for media_file in files:
        path = Path(media_file.absolute_path)
        if item.item_kind in {ZaisanKind.SERIES, ZaisanKind.SEASON}:
            findings.append(
                _finding(
                    "media_on_non_playable_item",
                    root,
                    item,
                    f"Media file {media_file.id} is attached directly to {item.item_kind.value} "
                    f"item {item.id}, which is a hierarchy container.",
                )
            )
        if item.item_kind is ZaisanKind.MOVIE and has_episode_marker(path.stem):
            findings.append(
                _finding(
                    "episode_file_as_movie",
                    root,
                    item,
                    f"Movie item {item.id} owns episode-like file {path.name!r}.",
                )
            )
        if (
            item.item_kind is ZaisanKind.EPISODE
            and not has_episode_marker(path.stem)
            and not any(_SEASON_OR_VOLUME_PATTERN.fullmatch(part) for part in path.parts)
        ):
            findings.append(
                _finding(
                    "movie_file_as_episode",
                    root,
                    item,
                    f"Episode item {item.id} owns file {path.name!r} with neither an episode "
                    "identifier nor season-directory context.",
                )
            )
    return tuple(findings)


def _duplicate_series_findings(root: Kura, items: Iterable[Zaisan]) -> tuple[AuditFinding, ...]:
    grouped: dict[str, list[Zaisan]] = defaultdict(list)
    for item in items:
        if item.item_kind is ZaisanKind.SERIES:
            grouped[_series_identity(item.title)].append(item)
    findings: list[AuditFinding] = []
    for identity, candidates in grouped.items():
        if len(candidates) < 2:
            continue
        item_ids = ", ".join(str(candidate.id) for candidate in candidates)
        titles = ", ".join(repr(candidate.title) for candidate in candidates)
        findings.append(
            AuditFinding(
                AuditCategory.AMBIGUOUS_STRUCTURE,
                Path(root.path),
                "[duplicate_series_minor_variation] "
                f"Series items {item_ids} normalise to {identity!r} ({titles}); "
                "review before merging.",
            )
        )
    return tuple(findings)


def _is_organisational_item(item: Zaisan, files: Sequence[MediaFile]) -> bool:
    title = item.title.casefold().strip()
    is_known_name = title in _ORGANISATIONAL_NAMES or _SEASON_OR_VOLUME_PATTERN.fullmatch(title)
    has_matching_path_context = any(
        _path_has_component(file.absolute_path, item.title) for file in files
    )
    return bool(is_known_name and has_matching_path_context)


def _has_unrelated_movie_files(item: Zaisan, files: Sequence[MediaFile]) -> bool:
    title_identity = _title_identity(item.title)
    file_identities = {_title_identity(Path(file.absolute_path).stem) for file in files}
    return any(title_identity not in identity for identity in file_identities)


def _path_has_component(path: str, component: str) -> bool:
    expected = component.casefold()
    return any(part.casefold() == expected for part in Path(path).parts)


def _series_identity(value: str) -> str:
    identity = _title_identity(value)
    return identity[3:] if identity.startswith("the") else identity


def _title_identity(value: str) -> str:
    return _TITLE_TOKEN_PATTERN.sub("", value.casefold())


def _playable_kinds() -> frozenset[ZaisanKind]:
    return frozenset({ZaisanKind.MOVIE, ZaisanKind.EPISODE, ZaisanKind.SPECIAL, ZaisanKind.EXTRA})


def _finding(code: str, root: Kura, item: Zaisan, explanation: str) -> AuditFinding:
    return AuditFinding(
        AuditCategory.AMBIGUOUS_STRUCTURE,
        Path(root.path),
        f"[{code}] {explanation}",
    )

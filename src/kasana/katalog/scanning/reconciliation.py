"""Persist a classified scan into Katalog's library model."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from kasana.katalog.container import canonical_container
from kasana.katalog.models import (
    AuditCategory,
    AuditIssue,
    AvailabilityState,
    Kura,
    MediaFile,
    Zaisan,
    ZaisanKind,
)
from kasana.katalog.parsing import ParsedMedia, ParsedMediaKind
from kasana.katalog.probe import ProbeResult
from kasana.katalog.scanning.classification import ExistingFile, PlanAction, PlannedFile
from kasana.katalog.scanning.discovery import AuditFinding, FileSnapshot, MediaSidecars
from kasana.katalog.scanning.local_metadata import LocalMetadata
from kasana.katalog.services import normalise_library_item_tags

type MovieIdentity = tuple[str, int | None]


@dataclass
class ItemCache:
    movies: dict[MovieIdentity, Zaisan] = field(default_factory=dict)
    movie_directories: dict[Path, Zaisan | None] = field(default_factory=dict)
    series: dict[str, Zaisan] = field(default_factory=dict)
    seasons: dict[tuple[int, int], Zaisan] = field(default_factory=dict)
    episodes: dict[tuple[int, int, int], Zaisan] = field(default_factory=dict)
    specials: dict[tuple[int, str], Zaisan] = field(default_factory=dict)
    extras: dict[tuple[int, str], Zaisan] = field(default_factory=dict)


def apply_scan(
    session: Session,
    root: Kura,
    plans: Sequence[PlannedFile],
    probe_results: Mapping[Path, ProbeResult],
    sidecars: Mapping[Path, MediaSidecars],
    unavailable_ids: frozenset[int],
    restored_ids: frozenset[int],
    existing_files: Sequence[ExistingFile],
    findings: Sequence[AuditFinding],
    completed_at: datetime,
) -> tuple[AuditFinding, ...]:
    """Apply all successful root changes in one transaction."""

    existing_by_id: dict[int, MediaFile] = {
        file.id: file
        for file in session.scalars(
            select(MediaFile).where(MediaFile.id.in_([record.id for record in existing_files]))
        ).all()
    }
    cache: ItemCache = item_cache(
        session.scalars(select(Zaisan).where(Zaisan.library_root_id == root.id)).all()
    )
    for file in existing_by_id.values():
        _cache_movie_directory(cache, file.library_item, Path(file.absolute_path))
    _prepare_movie_directories(session, root.id, cache, plans, existing_by_id, sidecars)
    scan_findings = list(findings)
    updated_file_ids: set[int] = set()
    for plan in plans:
        attachment = sidecars[plan.snapshot.path]
        if plan.action is PlanAction.MOVE:
            assert plan.existing_file_id is not None
            file = existing_by_id[plan.existing_file_id]
            try:
                update_file_location(session, cache, file, plan.snapshot, attachment)
            except LocalMetadataIdentityConflictError as error:
                scan_findings.append(_local_metadata_conflict_finding(attachment, error))
            _cache_movie_directory(cache, file.library_item, plan.snapshot.path)
            updated_file_ids.add(file.id)
            continue
        probe_result: ProbeResult = probe_results[plan.snapshot.path]
        if plan.action is PlanAction.CHANGE:
            assert plan.existing_file_id is not None
            file: MediaFile = existing_by_id[plan.existing_file_id]
            if plan.parsed is not None:
                file.library_item = materialise_item(
                    session,
                    root.id,
                    cache,
                    plan.parsed,
                    media_path=plan.snapshot.path,
                    local_metadata=attachment.metadata,
                )
            try:
                update_file_details(session, cache, file, plan.snapshot, probe_result, attachment)
            except LocalMetadataIdentityConflictError as error:
                scan_findings.append(_local_metadata_conflict_finding(attachment, error))
            _cache_movie_directory(cache, file.library_item, plan.snapshot.path)
            updated_file_ids.add(file.id)
            continue
        assert plan.parsed is not None
        item: Zaisan = materialise_item(
            session,
            root.id,
            cache,
            plan.parsed,
            media_path=plan.snapshot.path,
            local_metadata=attachment.metadata,
        )
        applied_sidecars = attachment
        try:
            apply_local_metadata(session, cache, item, attachment)
        except LocalMetadataIdentityConflictError as error:
            scan_findings.append(_local_metadata_conflict_finding(attachment, error))
            applied_sidecars = replace(attachment, metadata=None, metadata_path=None)
        session.add(media_file(item, plan.snapshot, probe_result, applied_sidecars))
        _cache_movie_directory(cache, item, plan.snapshot.path)
    for file in existing_by_id.values():
        if file.id in updated_file_ids:
            continue
        attachment = sidecars.get(Path(file.absolute_path))
        if attachment is not None:
            try:
                update_sidecars(session, cache, file, attachment)
            except LocalMetadataIdentityConflictError as error:
                scan_findings.append(_local_metadata_conflict_finding(attachment, error))
            _cache_movie_directory(cache, file.library_item, Path(file.absolute_path))
    for file_id in unavailable_ids:
        existing_by_id[file_id].availability = AvailabilityState.UNAVAILABLE
    for file_id in restored_ids:
        existing_by_id[file_id].availability = AvailabilityState.AVAILABLE
    root_record: Kura | None = session.get(Kura, root.id)
    if root_record is None:
        msg: str = f"Library root {root.id} does not exist."
        raise LookupError(msg)
    session.execute(delete(AuditIssue).where(AuditIssue.library_root_id == root.id))
    session.add_all(
        AuditIssue(
            library_root_id=root.id,
            category=finding.category,
            path=str(finding.path),
            message=finding.message,
            detected_at=completed_at,
        )
        for finding in scan_findings
    )
    root_record.last_scan_completed_at = completed_at
    session.flush()
    return tuple(scan_findings)


def item_cache(items: Iterable[Zaisan]) -> ItemCache:
    cache = ItemCache()
    item_list = list(items)
    by_id = {item.id: item for item in item_list}
    for item in item_list:
        title_key = item.sort_title.casefold()
        if item.item_kind is ZaisanKind.MOVIE and item.parent_id is None:
            cache.movies[_movie_identity(item.sort_title, item.release_year)] = item
        elif item.item_kind is ZaisanKind.SERIES:
            cache.series[title_key] = item
    for item in item_list:
        title_key = item.sort_title.casefold()
        parent = by_id.get(item.parent_id) if item.parent_id is not None else None
        if parent is None:
            continue
        if item.item_kind is ZaisanKind.SEASON and item.season_number is not None:
            cache.seasons[(_cache_item_identity(parent), item.season_number)] = item
        elif (
            item.item_kind is ZaisanKind.EPISODE
            and parent.item_kind is ZaisanKind.SEASON
            and item.season_number is not None
            and item.episode_number is not None
        ):
            series = by_id.get(parent.parent_id) if parent.parent_id is not None else None
            if series is not None:
                cache.episodes[
                    (_cache_item_identity(series), item.season_number, item.episode_number)
                ] = item
        elif item.item_kind is ZaisanKind.SPECIAL and parent.item_kind is ZaisanKind.SERIES:
            cache.specials[(_cache_item_identity(parent), title_key)] = item
        elif item.item_kind is ZaisanKind.EXTRA:
            cache.extras[(_cache_item_identity(parent), title_key)] = item
    return cache


def materialise_item(
    session: Session,
    root_id: int,
    cache: ItemCache,
    parsed: ParsedMedia,
    *,
    media_path: Path,
    local_metadata: LocalMetadata | None,
) -> Zaisan:
    match parsed.kind:
        case ParsedMediaKind.MOVIE:
            title, sort_title, release_year = _movie_values_from_local_metadata(
                parsed.title,
                parsed.release_year,
                local_metadata,
            )
            return get_movie(
                session,
                root_id,
                cache,
                title,
                release_year,
                sort_title=sort_title,
            )
        case ParsedMediaKind.EXTRA:
            if parsed.parent_movie_title is not None:
                parent = _movie_parent_for_extra(cache, media_path)
                if parent is None:
                    parent = get_movie(
                        session,
                        root_id,
                        cache,
                        parsed.parent_movie_title,
                        parsed.parent_movie_release_year,
                    )
            else:
                assert parsed.parent_series_title is not None
                parent = get_series(session, root_id, cache, parsed.parent_series_title)
            return get_extra(session, root_id, cache, parent, parsed.title)
        case ParsedMediaKind.SPECIAL:
            assert parsed.series_title is not None
            series = get_series(session, root_id, cache, parsed.series_title)
            key = (_cache_item_identity(series), parsed.title.casefold())
            special = cache.specials.get(key)
            if special is None:
                special = Zaisan(
                    library_root_id=root_id,
                    parent=series,
                    item_kind=ZaisanKind.SPECIAL,
                    title=parsed.title,
                    sort_title=parsed.title,
                    season_number=0,
                )
                session.add(special)
                cache.specials[key] = special
            return special
        case ParsedMediaKind.EPISODE:
            assert parsed.series_title is not None
            assert parsed.season_number is not None
            assert parsed.episode_number is not None
            series = get_series(session, root_id, cache, parsed.series_title)
            season_key = (_cache_item_identity(series), parsed.season_number)
            season = cache.seasons.get(season_key)
            if season is None:
                season = Zaisan(
                    library_root_id=root_id,
                    parent=series,
                    item_kind=ZaisanKind.SEASON,
                    title=f"Season {parsed.season_number}",
                    sort_title=f"Season {parsed.season_number}",
                    season_number=parsed.season_number,
                )
                session.add(season)
                cache.seasons[season_key] = season
            episode_key = (
                _cache_item_identity(series),
                parsed.season_number,
                parsed.episode_number,
            )
            episode = cache.episodes.get(episode_key)
            if episode is None:
                episode = Zaisan(
                    library_root_id=root_id,
                    parent=season,
                    item_kind=ZaisanKind.EPISODE,
                    title=parsed.title,
                    sort_title=parsed.title,
                    season_number=parsed.season_number,
                    episode_number=parsed.episode_number,
                    episode_end_season_number=parsed.episode_end_season_number,
                    episode_end_number=parsed.episode_end_number,
                )
                session.add(episode)
                cache.episodes[episode_key] = episode
            else:
                episode.episode_end_season_number = parsed.episode_end_season_number
                episode.episode_end_number = parsed.episode_end_number
            return episode


def get_movie(
    session: Session,
    root_id: int,
    cache: ItemCache,
    title: str,
    release_year: int | None = None,
    *,
    sort_title: str | None = None,
) -> Zaisan:
    item_sort_title = sort_title or title
    key = _movie_identity(item_sort_title, release_year)
    movie = cache.movies.get(key)
    if movie is None and release_year is not None:
        unknown_year_key = _movie_identity(item_sort_title, None)
        movie = cache.movies.pop(unknown_year_key, None)
        if movie is not None:
            movie.release_year = release_year
            cache.movies[key] = movie
    if movie is None:
        movie = Zaisan(
            library_root_id=root_id,
            item_kind=ZaisanKind.MOVIE,
            title=title,
            sort_title=item_sort_title,
            release_year=release_year,
        )
        session.add(movie)
        cache.movies[key] = movie
    return movie


def get_series(session: Session, root_id: int, cache: ItemCache, title: str) -> Zaisan:
    key = title.casefold()
    series = cache.series.get(key)
    if series is None:
        series = Zaisan(
            library_root_id=root_id,
            item_kind=ZaisanKind.SERIES,
            title=title,
            sort_title=title,
        )
        session.add(series)
        cache.series[key] = series
    return series


def get_extra(
    session: Session, root_id: int, cache: ItemCache, parent: Zaisan, title: str
) -> Zaisan:
    key = (_cache_item_identity(parent), title.casefold())
    extra = cache.extras.get(key)
    if extra is None:
        extra = Zaisan(
            library_root_id=root_id,
            parent=parent,
            item_kind=ZaisanKind.EXTRA,
            title=title,
            sort_title=title,
        )
        session.add(extra)
        cache.extras[key] = extra
    return extra


def _movie_identity(title: str, release_year: int | None) -> MovieIdentity:
    return title.casefold(), release_year


def _cache_item_identity(item: Zaisan) -> int:
    """Return one stable in-memory parent key for the duration of a scan.

    Newly materialised rows all have ``id is None`` until the transaction flushes.
    Their Python identity is stable both before and after that flush, unlike the
    database identifier.
    """

    return id(item)


def _prepare_movie_directories(
    session: Session,
    root_id: int,
    cache: ItemCache,
    plans: Sequence[PlannedFile],
    existing_by_id: Mapping[int, MediaFile],
    sidecars: Mapping[Path, MediaSidecars],
) -> None:
    """Make a title directory resolve to its known movie before extras are materialised."""

    for plan in plans:
        attachment = sidecars[plan.snapshot.path]
        if plan.action is PlanAction.ADD:
            assert plan.parsed is not None
            if plan.parsed.kind is not ParsedMediaKind.MOVIE:
                continue
            item = materialise_item(
                session,
                root_id,
                cache,
                plan.parsed,
                media_path=plan.snapshot.path,
                local_metadata=attachment.metadata,
            )
        else:
            assert plan.existing_file_id is not None
            file = existing_by_id[plan.existing_file_id]
            if plan.parsed is not None:
                if plan.parsed.kind is not ParsedMediaKind.MOVIE:
                    continue
                item = materialise_item(
                    session,
                    root_id,
                    cache,
                    plan.parsed,
                    media_path=plan.snapshot.path,
                    local_metadata=attachment.metadata,
                )
            else:
                item = file.library_item
        _cache_movie_directory(cache, item, plan.snapshot.path)


def _cache_movie_directory(cache: ItemCache, item: Zaisan, media_path: Path) -> None:
    """Associate a physical title directory with one unambiguous movie item."""

    if item.item_kind is not ZaisanKind.MOVIE or item.parent_id is not None:
        return
    directory = media_path.parent
    if directory not in cache.movie_directories:
        cache.movie_directories[directory] = item
        return
    known = cache.movie_directories[directory]
    if known is not item:
        cache.movie_directories[directory] = None


def _movie_parent_for_extra(cache: ItemCache, media_path: Path) -> Zaisan | None:
    directory = (
        media_path.parent.parent
        if media_path.parent.name.casefold() == "extras"
        else media_path.parent
    )
    return cache.movie_directories.get(directory)


def _movie_values_from_local_metadata(
    title: str,
    release_year: int | None,
    metadata: LocalMetadata | None,
) -> tuple[str, str, int | None]:
    if metadata is None:
        return title, title, release_year
    item_title = metadata.title if metadata.title is not None else title
    item_sort_title = metadata.sort_title if metadata.sort_title is not None else item_title
    item_release_year = (
        metadata.year
        if metadata.year is not None
        else metadata.release_date.year
        if metadata.release_date is not None
        else release_year
    )
    return item_title, item_sort_title, item_release_year


class LocalMetadataIdentityConflictError(ValueError):
    """A sidecar would violate a unique library-item identity."""


def _local_metadata_conflict_finding(
    sidecars: MediaSidecars, error: LocalMetadataIdentityConflictError
) -> AuditFinding:
    assert sidecars.metadata_path is not None
    return AuditFinding(AuditCategory.INVALID_METADATA_SIDECAR, sidecars.metadata_path, str(error))


def _effective_local_metadata_identity(
    item: Zaisan, metadata: LocalMetadata
) -> tuple[str, str, int | None]:
    item_title = item.title
    item_sort_title = item.sort_title
    item_release_year = item.release_year
    if metadata.title is not None:
        item_title = metadata.title
        if "sort_title" not in metadata.model_fields_set:
            item_sort_title = metadata.title
    if metadata.sort_title is not None:
        item_sort_title = metadata.sort_title
    if metadata.release_date is not None:
        item_release_year = metadata.release_date.year
    if metadata.year is not None:
        item_release_year = metadata.year
    return item_title, item_sort_title, item_release_year


def _assert_local_metadata_identity_available(
    session: Session,
    item: Zaisan,
    *,
    sort_title: str,
    release_year: int | None,
) -> None:
    if item.item_kind is ZaisanKind.EPISODE or (
        item.sort_title == sort_title and item.release_year == release_year
    ):
        return
    session.flush()
    statement = select(Zaisan).where(
        Zaisan.library_root_id == item.library_root_id,
        Zaisan.item_kind == item.item_kind,
        Zaisan.id != item.id,
    )
    if item.item_kind is ZaisanKind.MOVIE:
        statement = statement.where(Zaisan.parent_id.is_(None))
        statement = statement.where(
            Zaisan.release_year.is_(None)
            if release_year is None
            else Zaisan.release_year == release_year
        )
    elif item.parent_id is None:
        statement = statement.where(Zaisan.parent_id.is_(None))
    else:
        statement = statement.where(Zaisan.parent_id == item.parent_id)
    if not any(
        candidate.sort_title.casefold() == sort_title.casefold()
        for candidate in session.scalars(statement)
    ):
        return
    suffix = f" ({release_year})" if item.item_kind is ZaisanKind.MOVIE else ""
    raise LocalMetadataIdentityConflictError(
        "Local metadata would duplicate the "
        f"{item.item_kind.value} identity {sort_title!r}{suffix}."
    )


def _refresh_item_cache_identity(
    cache: ItemCache,
    item: Zaisan,
    *,
    previous_sort_title: str,
    previous_release_year: int | None,
) -> None:
    old_title = previous_sort_title.casefold()
    new_title = item.sort_title.casefold()
    if item.item_kind is ZaisanKind.MOVIE and item.parent_id is None:
        old_key = _movie_identity(old_title, previous_release_year)
        new_key = _movie_identity(new_title, item.release_year)
        _replace_cache_identity(cache.movies, old_key, new_key, item)
    elif item.item_kind is ZaisanKind.SERIES and item.parent_id is None:
        _replace_cache_identity(cache.series, old_title, new_title, item)
    elif item.item_kind in {ZaisanKind.SPECIAL, ZaisanKind.EXTRA} and item.parent is not None:
        parent_key = _cache_item_identity(item.parent)
        identities = cache.specials if item.item_kind is ZaisanKind.SPECIAL else cache.extras
        _replace_cache_identity(
            identities,
            (parent_key, old_title),
            (parent_key, new_title),
            item,
        )


def _replace_cache_identity[K](
    identities: dict[K, Zaisan], old_key: K, new_key: K, item: Zaisan
) -> None:
    if identities.get(old_key) is item:
        del identities[old_key]
    existing = identities.get(new_key)
    if existing is not None and existing is not item:
        raise RuntimeError("A validated library-item identity is already cached by another item.")
    identities[new_key] = item


def media_file(
    item: Zaisan, snapshot: FileSnapshot, probe: ProbeResult, sidecars: MediaSidecars
) -> MediaFile:
    return MediaFile(
        library_item=item,
        absolute_path=str(snapshot.path),
        size_bytes=snapshot.size_bytes,
        mtime_ns=snapshot.mtime_ns,
        filesystem_device=snapshot.filesystem_device,
        filesystem_inode=snapshot.filesystem_inode,
        container=canonical_container(probe.container) or probe.container,
        duration_seconds=probe.duration_seconds,
        video_streams=list(probe.video_streams),
        attached_pictures=list(probe.attached_pictures),
        audio_streams=list(probe.audio_streams),
        subtitle_streams=list(probe.subtitle_streams),
        font_attachments=list(probe.font_attachments),
        local_poster_path=str(sidecars.poster) if sidecars.poster is not None else None,
        local_metadata_path=(
            str(sidecars.metadata_path) if sidecars.metadata_path is not None else None
        ),
        subtitle_sidecar_paths=[str(path) for path in sidecars.subtitles],
        availability=AvailabilityState.AVAILABLE,
    )


def update_file_location(
    session: Session,
    cache: ItemCache,
    file: MediaFile,
    snapshot: FileSnapshot,
    sidecars: MediaSidecars,
) -> None:
    _update_file_location(file, snapshot)
    update_sidecars(session, cache, file, sidecars)


def _update_file_location(file: MediaFile, snapshot: FileSnapshot) -> None:
    file.absolute_path = str(snapshot.path)
    file.size_bytes = snapshot.size_bytes
    file.mtime_ns = snapshot.mtime_ns
    file.filesystem_device = snapshot.filesystem_device
    file.filesystem_inode = snapshot.filesystem_inode
    file.availability = AvailabilityState.AVAILABLE


def update_file_details(
    session: Session,
    cache: ItemCache,
    file: MediaFile,
    snapshot: FileSnapshot,
    probe: ProbeResult,
    sidecars: MediaSidecars,
) -> None:
    _update_file_location(file, snapshot)
    file.container = canonical_container(probe.container) or probe.container
    file.duration_seconds = probe.duration_seconds
    file.video_streams = list(probe.video_streams)
    file.attached_pictures = list(probe.attached_pictures)
    file.audio_streams = list(probe.audio_streams)
    file.subtitle_streams = list(probe.subtitle_streams)
    file.font_attachments = list(probe.font_attachments)
    update_sidecars(session, cache, file, sidecars)


def update_sidecars(
    session: Session, cache: ItemCache, file: MediaFile, sidecars: MediaSidecars
) -> None:
    """Persist only local sidecars unambiguously attached during this scan."""

    file.local_poster_path = str(sidecars.poster) if sidecars.poster is not None else None
    file.subtitle_sidecar_paths = [str(path) for path in sidecars.subtitles]
    apply_local_metadata(session, cache, file.library_item, sidecars)
    if sidecars.metadata_path is not None:
        file.local_metadata_path = str(sidecars.metadata_path)


def apply_local_metadata(
    session: Session, cache: ItemCache, item: Zaisan, sidecars: MediaSidecars
) -> None:
    """Apply one validated local source without creating a provider binding."""

    metadata = sidecars.metadata
    if metadata is None:
        return
    previous_sort_title = item.sort_title
    previous_release_year = item.release_year
    title, sort_title, release_year = _effective_local_metadata_identity(item, metadata)
    _assert_local_metadata_identity_available(
        session,
        item,
        sort_title=sort_title,
        release_year=release_year,
    )
    item.title = title
    item.sort_title = sort_title
    item.release_year = release_year
    if metadata.release_date is not None:
        item.release_date = metadata.release_date
    if metadata.overview is not None:
        item.overview = metadata.overview
    if metadata.tags is not None:
        item.tags = normalise_library_item_tags(metadata.tags)
    if metadata.external_ids is not None:
        item.local_external_ids = [
            {"namespace": identifier.namespace, "value": identifier.value}
            for identifier in metadata.external_ids
        ]
    _refresh_item_cache_identity(
        cache,
        item,
        previous_sort_title=previous_sort_title,
        previous_release_year=previous_release_year,
    )

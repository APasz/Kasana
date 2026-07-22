"""Persist a classified scan into Katalog's library model."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from kasana.katalog.container import canonical_container
from kasana.katalog.models import (
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


@dataclass
class ItemCache:
    movies: dict[str, Zaisan] = field(default_factory=dict)
    series: dict[str, Zaisan] = field(default_factory=dict)
    seasons: dict[tuple[str, int], Zaisan] = field(default_factory=dict)
    episodes: dict[tuple[str, int, int], Zaisan] = field(default_factory=dict)
    specials: dict[tuple[str, str], Zaisan] = field(default_factory=dict)
    extras: dict[tuple[str, str], Zaisan] = field(default_factory=dict)


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
) -> None:
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
    for plan in plans:
        if plan.action is PlanAction.MOVE:
            assert plan.existing_file_id is not None
            update_file_location(
                existing_by_id[plan.existing_file_id], plan.snapshot, sidecars[plan.snapshot.path]
            )
            continue
        probe_result: ProbeResult = probe_results[plan.snapshot.path]
        if plan.action is PlanAction.CHANGE:
            assert plan.existing_file_id is not None
            file: MediaFile = existing_by_id[plan.existing_file_id]
            if plan.parsed is not None:
                file.library_item = materialise_item(session, root.id, cache, plan.parsed)
            update_file_details(file, plan.snapshot, probe_result, sidecars[plan.snapshot.path])
            continue
        assert plan.parsed is not None
        item: Zaisan = materialise_item(session, root.id, cache, plan.parsed)
        session.add(media_file(item, plan.snapshot, probe_result, sidecars[plan.snapshot.path]))
    for file in existing_by_id.values():
        attachment = sidecars.get(Path(file.absolute_path))
        if attachment is not None:
            update_sidecars(file, attachment)
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
        for finding in findings
    )
    root_record.last_scan_completed_at = completed_at
    session.flush()


def item_cache(items: Iterable[Zaisan]) -> ItemCache:
    cache = ItemCache()
    item_list = list(items)
    by_id = {item.id: item for item in item_list}
    for item in item_list:
        title_key = item.sort_title.casefold()
        if item.item_kind is ZaisanKind.MOVIE and item.parent_id is None:
            cache.movies[title_key] = item
        elif item.item_kind is ZaisanKind.SERIES:
            cache.series[title_key] = item
    for item in item_list:
        title_key = item.sort_title.casefold()
        parent = by_id.get(item.parent_id) if item.parent_id is not None else None
        if parent is None:
            continue
        parent_key = parent.sort_title.casefold()
        if item.item_kind is ZaisanKind.SEASON and item.season_number is not None:
            cache.seasons[(parent_key, item.season_number)] = item
        elif (
            item.item_kind is ZaisanKind.EPISODE
            and parent.item_kind is ZaisanKind.SEASON
            and item.season_number is not None
            and item.episode_number is not None
        ):
            series = by_id.get(parent.parent_id) if parent.parent_id is not None else None
            if series is not None:
                cache.episodes[
                    (series.sort_title.casefold(), item.season_number, item.episode_number)
                ] = item
        elif item.item_kind is ZaisanKind.SPECIAL and parent.item_kind is ZaisanKind.SERIES:
            cache.specials[(parent_key, title_key)] = item
        elif item.item_kind is ZaisanKind.EXTRA:
            cache.extras[(parent_key, title_key)] = item
    return cache


def materialise_item(
    session: Session, root_id: int, cache: ItemCache, parsed: ParsedMedia
) -> Zaisan:
    match parsed.kind:
        case ParsedMediaKind.MOVIE:
            return get_movie(session, root_id, cache, parsed.title, parsed.release_year)
        case ParsedMediaKind.EXTRA:
            if parsed.parent_movie_title is not None:
                parent = get_movie(session, root_id, cache, parsed.parent_movie_title)
            else:
                assert parsed.parent_series_title is not None
                parent = get_series(session, root_id, cache, parsed.parent_series_title)
            return get_extra(session, root_id, cache, parent, parsed.title)
        case ParsedMediaKind.SPECIAL:
            assert parsed.series_title is not None
            series = get_series(session, root_id, cache, parsed.series_title)
            key = (series.sort_title.casefold(), parsed.title.casefold())
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
            season_key = (series.sort_title.casefold(), parsed.season_number)
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
                series.sort_title.casefold(),
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
                )
                session.add(episode)
                cache.episodes[episode_key] = episode
            return episode


def get_movie(
    session: Session,
    root_id: int,
    cache: ItemCache,
    title: str,
    release_year: int | None = None,
) -> Zaisan:
    key = title.casefold()
    movie = cache.movies.get(key)
    if movie is None:
        movie = Zaisan(
            library_root_id=root_id,
            item_kind=ZaisanKind.MOVIE,
            title=title,
            sort_title=title,
            release_year=release_year,
        )
        session.add(movie)
        cache.movies[key] = movie
    elif movie.release_year is None and release_year is not None:
        movie.release_year = release_year
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
    key = (parent.sort_title.casefold(), title.casefold())
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
        local_poster_path=str(sidecars.poster) if sidecars.poster is not None else None,
        subtitle_sidecar_paths=[str(path) for path in sidecars.subtitles],
        availability=AvailabilityState.AVAILABLE,
    )


def update_file_location(file: MediaFile, snapshot: FileSnapshot, sidecars: MediaSidecars) -> None:
    file.absolute_path = str(snapshot.path)
    file.size_bytes = snapshot.size_bytes
    file.mtime_ns = snapshot.mtime_ns
    file.filesystem_device = snapshot.filesystem_device
    file.filesystem_inode = snapshot.filesystem_inode
    file.availability = AvailabilityState.AVAILABLE
    update_sidecars(file, sidecars)


def update_file_details(
    file: MediaFile, snapshot: FileSnapshot, probe: ProbeResult, sidecars: MediaSidecars
) -> None:
    update_file_location(file, snapshot, sidecars)
    file.container = canonical_container(probe.container) or probe.container
    file.duration_seconds = probe.duration_seconds
    file.video_streams = list(probe.video_streams)
    file.attached_pictures = list(probe.attached_pictures)
    file.audio_streams = list(probe.audio_streams)
    file.subtitle_streams = list(probe.subtitle_streams)


def update_sidecars(file: MediaFile, sidecars: MediaSidecars) -> None:
    """Persist only local sidecars unambiguously attached during this scan."""

    file.local_poster_path = str(sidecars.poster) if sidecars.poster is not None else None
    file.subtitle_sidecar_paths = [str(path) for path in sidecars.subtitles]

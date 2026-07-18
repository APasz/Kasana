"""Focused persistence operations for Katalog's initial domain use cases."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import LiteralString

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from kasana.katalog.models import (
    AvailabilityState,
    Collection,
    CollectionKin,
    JSONObject,
    Keiro,
    KeiroEntry,
    KeiroKind,
    Kinship,
    Kura,
    MediaFile,
    MetadataField,
    PlaybackState,
    User,
    Zaisan,
    ZaisanKind,
)

_PARENT_KINDS: dict[ZaisanKind, frozenset[ZaisanKind]] = {
    ZaisanKind.SEASON: frozenset[ZaisanKind]({ZaisanKind.SERIES}),
    ZaisanKind.EPISODE: frozenset[ZaisanKind]({ZaisanKind.SEASON}),
    ZaisanKind.SPECIAL: frozenset[ZaisanKind]({ZaisanKind.SERIES, ZaisanKind.SEASON}),
    ZaisanKind.EXTRA: frozenset[ZaisanKind](
        {
            ZaisanKind.MOVIE,
            ZaisanKind.SERIES,
            ZaisanKind.SEASON,
            ZaisanKind.EPISODE,
            ZaisanKind.SPECIAL,
        }
    ),
}
_PLAYABLE_KINDS: frozenset[ZaisanKind] = frozenset[ZaisanKind](
    {ZaisanKind.MOVIE, ZaisanKind.EPISODE, ZaisanKind.SPECIAL, ZaisanKind.EXTRA}
)


def create_library_root(
    session: Session,
    *,
    path: Path,
    expected_media_kind: ZaisanKind,
    default_tags: frozenset[str] = frozenset[str](),
    enabled: bool = True,
    display_name: str | None = None,
) -> Kura:
    if not path.is_absolute():
        msg = "A library root path must be absolute."
        raise ValueError(msg)
    root: Kura = Kura(
        path=str(path),
        expected_media_kind=expected_media_kind,
        default_tags=sorted(default_tags),
        enabled=enabled,
        display_name=display_name.strip() if display_name is not None else None,
    )
    session.add(root)
    session.flush()
    return root


def create_library_item(
    session: Session,
    *,
    library_root_id: int,
    item_kind: ZaisanKind,
    title: str,
    sort_title: str | None = None,
    parent_id: int | None = None,
    release_year: int | None = None,
    release_date: date | None = None,
    season_number: int | None = None,
    episode_number: int | None = None,
    overview: str | None = None,
    availability: AvailabilityState = AvailabilityState.AVAILABLE,
    locked_metadata_fields: frozenset[MetadataField] = frozenset(),
) -> Zaisan:
    normalized_title = title.strip()
    if not normalized_title:
        msg = "A library item title cannot be empty."
        raise ValueError(msg)
    if item_kind is ZaisanKind.SEASON and season_number is None:
        msg = "A season requires a season number."
        raise ValueError(msg)
    if item_kind is ZaisanKind.EPISODE and (season_number is None or episode_number is None):
        msg = "An episode requires season and episode numbers."
        raise ValueError(msg)

    _validate_parent(session, library_root_id, item_kind, parent_id)
    item: Zaisan = Zaisan(
        library_root_id=library_root_id,
        parent_id=parent_id,
        item_kind=item_kind,
        title=normalized_title,
        sort_title=(sort_title or normalized_title).strip(),
        release_year=release_year,
        release_date=release_date,
        season_number=season_number,
        episode_number=episode_number,
        overview=overview,
        availability=availability,
        locked_metadata_fields=sorted(field.value for field in locked_metadata_fields),
    )
    session.add(item)
    session.flush()
    return item


def attach_media_file(
    session: Session,
    *,
    library_item_id: int,
    absolute_path: Path,
    size_bytes: int,
    mtime_ns: int,
    container: str,
    filesystem_device: int | None = None,
    filesystem_inode: int | None = None,
    duration_seconds: float | None = None,
    video_streams: Sequence[JSONObject] = (),
    attached_pictures: Sequence[JSONObject] = (),
    audio_streams: Sequence[JSONObject] = (),
    subtitle_streams: Sequence[JSONObject] = (),
    availability: AvailabilityState = AvailabilityState.AVAILABLE,
) -> MediaFile:
    item: Zaisan = _require_item(session, library_item_id)
    if item.item_kind not in _PLAYABLE_KINDS:
        msg: LiteralString = f"{item.item_kind.value} items cannot own playable media files."
        raise ValueError(msg)
    if not absolute_path.is_absolute():
        msg = "A media file path must be absolute."
        raise ValueError(msg)
    file: MediaFile = MediaFile(
        library_item_id=library_item_id,
        absolute_path=str(absolute_path),
        size_bytes=size_bytes,
        mtime_ns=mtime_ns,
        filesystem_device=filesystem_device,
        filesystem_inode=filesystem_inode,
        container=container,
        duration_seconds=duration_seconds,
        video_streams=list[JSONObject](video_streams),
        attached_pictures=list[JSONObject](attached_pictures),
        audio_streams=list[JSONObject](audio_streams),
        subtitle_streams=list[JSONObject](subtitle_streams),
        availability=availability,
    )
    session.add(file)
    session.flush()
    return file


def create_collection(session: Session, *, name: str, overview: str | None = None) -> Collection:
    collection: Collection = Collection(
        name=_require_text(name, "A collection name"), overview=overview
    )
    session.add(collection)
    session.flush()
    return collection


def add_collection_membership(
    session: Session,
    *,
    collection_id: int,
    library_item_id: int,
    relationship: Kinship | None = None,
) -> CollectionKin:
    membership: CollectionKin = CollectionKin(
        collection_id=collection_id,
        library_item_id=library_item_id,
        relationship=relationship,
    )
    session.add(membership)
    session.flush()
    return membership


def create_watch_order(
    session: Session,
    *,
    collection_id: int,
    name: str,
    order_kind: KeiroKind,
) -> Keiro:
    watch_order: Keiro = Keiro(
        collection_id=collection_id,
        name=_require_text(name, "A watch order name"),
        order_kind=order_kind,
    )
    session.add(watch_order)
    session.flush()
    return watch_order


def append_watch_order_entry(
    session: Session, *, watch_order_id: int, library_item_id: int
) -> KeiroEntry:
    item: Zaisan = _require_item(session, library_item_id)
    if item.item_kind not in _PLAYABLE_KINDS:
        msg: LiteralString = f"{item.item_kind.value} items cannot appear in a watch order."
        raise ValueError(msg)
    highest_position: int | None = session.scalar(
        select(func.max(KeiroEntry.position)).where(KeiroEntry.watch_order_id == watch_order_id)
    )
    entry: KeiroEntry = KeiroEntry(
        watch_order_id=watch_order_id,
        library_item_id=library_item_id,
        position=0 if highest_position is None else highest_position + 1,
    )
    session.add(entry)
    session.flush()
    return entry


def create_user(session: Session, *, username: str, display_name: str | None = None) -> User:
    user: User = User(username=_require_text(username, "A username"), display_name=display_name)
    session.add(user)
    session.flush()
    return user


def record_playback_progress(
    session: Session,
    *,
    user_id: int,
    library_item_id: int,
    position_seconds: float,
    duration_seconds: float,
    completed: bool,
    increment_play_count: bool = False,
    played_at: datetime | None = None,
) -> PlaybackState:
    item: Zaisan = _require_item(session, library_item_id)
    if item.item_kind not in _PLAYABLE_KINDS:
        msg: LiteralString = f"{item.item_kind.value} items cannot have playback state."
        raise ValueError(msg)
    if position_seconds < 0 or duration_seconds < 0 or position_seconds > duration_seconds:
        msg = "Playback position must be between zero and its duration."
        raise ValueError(msg)
    timestamp: datetime = played_at or datetime.now(UTC)
    state: PlaybackState | None = session.scalar(
        select(PlaybackState).where(
            PlaybackState.user_id == user_id,
            PlaybackState.library_item_id == library_item_id,
        )
    )
    if state is None:
        state = PlaybackState(
            user_id=user_id,
            library_item_id=library_item_id,
            position_seconds=position_seconds,
            duration_seconds=duration_seconds,
            completed=completed,
            play_count=int(increment_play_count),
            last_played_at=timestamp,
        )
        session.add(state)
    else:
        state.position_seconds = position_seconds
        state.duration_seconds = duration_seconds
        state.completed = completed
        state.play_count += int(increment_play_count)
        state.last_played_at = timestamp
    session.flush()
    return state


def set_media_file_availability(
    session: Session, *, media_file_id: int, availability: AvailabilityState
) -> MediaFile:
    media_file: MediaFile | None = session.get(MediaFile, media_file_id)
    if media_file is None:
        msg: str = f"Media file {media_file_id} does not exist."
        raise LookupError(msg)
    media_file.availability = availability
    session.flush()
    return media_file


def delete_library_item(session: Session, *, library_item_id: int) -> None:
    item: Zaisan = _require_item(session, library_item_id)
    session.delete(item)
    session.flush()


def _validate_parent(
    session: Session,
    library_root_id: int,
    item_kind: ZaisanKind,
    parent_id: int | None,
) -> None:
    allowed_parent_kinds: frozenset[ZaisanKind] | None = _PARENT_KINDS.get(item_kind)
    if parent_id is None:
        if allowed_parent_kinds is not None:
            msg: LiteralString = f"{item_kind.value} items require a parent."
            raise ValueError(msg)
        return
    if allowed_parent_kinds is None:
        msg = f"{item_kind.value} items cannot have a parent."
        raise ValueError(msg)
    parent = _require_item(session, parent_id)
    if parent.library_root_id != library_root_id:
        msg = "A library item's parent must be in the same library root."
        raise ValueError(msg)
    if parent.item_kind not in allowed_parent_kinds:
        expected = ", ".join(kind.value for kind in sorted(allowed_parent_kinds))
        msg = f"{item_kind.value} requires one of these parent kinds: {expected}."
        raise ValueError(msg)


def _require_item(session: Session, library_item_id: int) -> Zaisan:
    item: Zaisan | None = session.get(Zaisan, library_item_id)
    if item is None:
        msg = f"Library item {library_item_id} does not exist."
        raise LookupError(msg)
    return item


def _require_text(value: str, description: str) -> str:
    normalized = value.strip()
    if not normalized:
        msg = f"{description} cannot be empty."
        raise ValueError(msg)
    return normalized

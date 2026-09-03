"""Focused persistence operations for Katalog's initial domain use cases."""

from __future__ import annotations

from collections.abc import Collection as AbstractCollection
from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import LiteralString

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from kasana.katalog.limits import MAX_PLAYBACK_STATE_BATCH_SIZE
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
PLAYABLE_ITEM_KINDS: frozenset[ZaisanKind] = frozenset[ZaisanKind](
    {ZaisanKind.MOVIE, ZaisanKind.EPISODE, ZaisanKind.SPECIAL, ZaisanKind.EXTRA}
)
EPISODIC_ITEM_KINDS: frozenset[ZaisanKind] = frozenset[ZaisanKind](
    {ZaisanKind.EPISODE, ZaisanKind.SPECIAL}
)
_SERIES_COMPLETION_CHILD_KINDS: frozenset[ZaisanKind] = frozenset[ZaisanKind](
    {ZaisanKind.SEASON, ZaisanKind.SPECIAL}
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
    air_date: date | None = None,
    season_number: int | None = None,
    episode_number: int | None = None,
    overview: str | None = None,
    tags: frozenset[str] = frozenset(),
    availability: AvailabilityState = AvailabilityState.AVAILABLE,
    locked_metadata_fields: frozenset[MetadataField] = frozenset(),
) -> Zaisan:
    normalised_title = title.strip()
    if not normalised_title:
        msg = "A library item title cannot be empty."
        raise ValueError(msg)
    if item_kind is ZaisanKind.SEASON and season_number is None:
        msg = "A season requires a season number."
        raise ValueError(msg)
    if item_kind is ZaisanKind.EPISODE and (season_number is None or episode_number is None):
        msg = "An episode requires season and episode numbers."
        raise ValueError(msg)

    validate_library_item_parent(session, library_root_id, item_kind, parent_id)
    item: Zaisan = Zaisan(
        library_root_id=library_root_id,
        parent_id=parent_id,
        item_kind=item_kind,
        title=normalised_title,
        sort_title=(sort_title or normalised_title).strip(),
        release_year=release_year,
        release_date=release_date,
        air_date=air_date,
        season_number=season_number,
        episode_number=episode_number,
        overview=overview,
        tags=normalise_library_item_tags(tags),
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
    font_attachments: Sequence[JSONObject] = (),
    availability: AvailabilityState = AvailabilityState.AVAILABLE,
) -> MediaFile:
    item: Zaisan = _require_item(session, library_item_id)
    if item.item_kind not in PLAYABLE_ITEM_KINDS:
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
        font_attachments=list[JSONObject](font_attachments),
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
    collection = session.get(Collection, collection_id)
    if collection is None:
        raise ValueError("The collection does not exist.")
    if collection.default_watch_order_id is None:
        collection.default_watch_order_id = watch_order.id
    return watch_order


def append_watch_order_entry(
    session: Session, *, watch_order_id: int, library_item_id: int
) -> KeiroEntry:
    item: Zaisan = _require_item(session, library_item_id)
    if item.item_kind not in PLAYABLE_ITEM_KINDS:
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
    _require_playback_state_item(item)
    _require_playback_position(position_seconds, duration_seconds)
    timestamp: datetime = played_at or datetime.now(UTC)
    state: PlaybackState | None = session.scalar(
        select(PlaybackState).where(
            PlaybackState.user_id == user_id,
            PlaybackState.library_item_id == library_item_id,
        )
    )
    state = _store_playback_progress(
        session,
        user_id=user_id,
        library_item_id=library_item_id,
        existing_state=state,
        position_seconds=position_seconds,
        duration_seconds=duration_seconds,
        completed=completed,
        increment_play_count=increment_play_count,
        played_at=timestamp,
    )
    session.flush()
    synchronise_parent_completion(session, user_id=user_id, item=item, completed_at=timestamp)
    bump_playback_state_revision(session, user_id=user_id)
    return state


def mark_playback_items_watched(
    session: Session,
    *,
    user_id: int,
    items: Sequence[Zaisan],
    watched_at: datetime | None = None,
) -> None:
    """Mark playable items watched with bounded queries and one aggregate roll-up."""

    if not items:
        return
    item_ids = tuple(item.id for item in items)
    if len(set(item_ids)) != len(item_ids):
        raise ValueError("Watched items must be unique.")
    for item in items:
        _require_playback_state_item(item)
    timestamp = watched_at or datetime.now(UTC)
    durations_by_item_id: dict[int, float] = {}
    states_by_item_id: dict[int, PlaybackState] = {}
    for item_id_batch in _item_id_batches(item_ids):
        durations_by_item_id.update(
            {
                library_item_id: duration_seconds or 0.0
                for library_item_id, duration_seconds in session.execute(
                    select(MediaFile.library_item_id, func.max(MediaFile.duration_seconds))
                    .where(MediaFile.library_item_id.in_(item_id_batch))
                    .group_by(MediaFile.library_item_id)
                )
            }
        )
        states_by_item_id.update(
            {
                state.library_item_id: state
                for state in session.scalars(
                    select(PlaybackState).where(
                        PlaybackState.user_id == user_id,
                        PlaybackState.library_item_id.in_(item_id_batch),
                    )
                )
            }
        )
    for item in items:
        duration_seconds = durations_by_item_id.get(item.id, 0.0)
        _store_playback_progress(
            session,
            user_id=user_id,
            library_item_id=item.id,
            existing_state=states_by_item_id.get(item.id),
            position_seconds=duration_seconds,
            duration_seconds=duration_seconds,
            completed=True,
            increment_play_count=True,
            played_at=timestamp,
        )
    session.flush()
    synchronise_parent_completions(
        session,
        user_id=user_id,
        items=items,
        completed_at=timestamp,
    )
    bump_playback_state_revision(session, user_id=user_id)


def clear_playback_items(
    session: Session,
    *,
    user_id: int,
    items: Sequence[Zaisan],
    container: Zaisan,
) -> None:
    """Clear direct and derived watched state, then recompute affected containers."""

    state_item_ids = tuple(sorted({container.id, *(item.id for item in items)}))
    for item_id_batch in _item_id_batches(state_item_ids):
        states = tuple(
            session.scalars(
                select(PlaybackState).where(
                    PlaybackState.user_id == user_id,
                    PlaybackState.library_item_id.in_(item_id_batch),
                )
            )
        )
        for state in states:
            session.delete(state)
    session.flush()
    synchronise_parent_completions(
        session,
        user_id=user_id,
        items=(*items, container),
    )
    bump_playback_state_revision(session, user_id=user_id)


def synchronise_parent_completion(
    session: Session,
    *,
    user_id: int,
    item: Zaisan,
    completed_at: datetime | None = None,
) -> None:
    """Keep season and series completion aligned with their watched children."""

    synchronise_parent_completions(
        session,
        user_id=user_id,
        items=(item,),
        completed_at=completed_at,
    )


def synchronise_parent_completions(
    session: Session,
    *,
    user_id: int,
    items: Sequence[Zaisan],
    completed_at: datetime | None = None,
) -> None:
    """Synchronise each affected season and series once after a bulk mutation."""

    seasons, series = _completion_containers(session, items)
    for season in seasons.values():
        _synchronise_season_completion(
            session,
            user_id=user_id,
            season=season,
            completed_at=completed_at,
        )
    for series_item in series.values():
        _synchronise_series_completion(
            session,
            user_id=user_id,
            series=series_item,
            completed_at=completed_at,
        )


def bump_playback_state_revision(session: Session, *, user_id: int) -> None:
    """Advance a viewer's state revision after one persisted playback mutation."""

    user: User | None = session.get(User, user_id)
    if user is None:
        msg = f"User {user_id} does not exist."
        raise LookupError(msg)
    user.playback_state_revision += 1


def _store_playback_progress(
    session: Session,
    *,
    user_id: int,
    library_item_id: int,
    existing_state: PlaybackState | None,
    position_seconds: float,
    duration_seconds: float,
    completed: bool,
    increment_play_count: bool,
    played_at: datetime,
) -> PlaybackState:
    if existing_state is None:
        state = PlaybackState(
            user_id=user_id,
            library_item_id=library_item_id,
            position_seconds=position_seconds,
            duration_seconds=duration_seconds,
            completed=completed,
            play_count=int(increment_play_count),
            last_played_at=played_at,
        )
        session.add(state)
        return state
    existing_state.position_seconds = position_seconds
    existing_state.duration_seconds = duration_seconds
    existing_state.completed = completed
    existing_state.play_count += int(increment_play_count)
    existing_state.last_played_at = played_at
    return existing_state


def _require_playback_state_item(item: Zaisan) -> None:
    if item.item_kind not in PLAYABLE_ITEM_KINDS:
        msg: LiteralString = f"{item.item_kind.value} items cannot have playback state."
        raise ValueError(msg)


def _require_playback_position(position_seconds: float, duration_seconds: float) -> None:
    if position_seconds < 0 or duration_seconds < 0 or position_seconds > duration_seconds:
        msg = "Playback position must be between zero and its duration."
        raise ValueError(msg)


def _item_id_batches(item_ids: tuple[int, ...]) -> Iterator[tuple[int, ...]]:
    for start in range(0, len(item_ids), MAX_PLAYBACK_STATE_BATCH_SIZE):
        yield item_ids[start : start + MAX_PLAYBACK_STATE_BATCH_SIZE]


def _completion_containers(
    session: Session,
    items: Sequence[Zaisan],
) -> tuple[dict[int, Zaisan], dict[int, Zaisan]]:
    seasons: dict[int, Zaisan] = {}
    series: dict[int, Zaisan] = {}
    parent_ids = {
        item.parent_id
        for item in items
        if item.parent_id is not None
        and (item.item_kind is ZaisanKind.SEASON or item.item_kind in EPISODIC_ITEM_KINDS)
    }
    parents_by_id = (
        {
            parent.id: parent
            for parent in session.scalars(select(Zaisan).where(Zaisan.id.in_(parent_ids)))
        }
        if parent_ids
        else {}
    )
    for item in items:
        if item.item_kind is ZaisanKind.SERIES:
            series[item.id] = item
            continue
        if item.item_kind is ZaisanKind.SEASON:
            seasons[item.id] = item
            continue
        if item.item_kind not in EPISODIC_ITEM_KINDS:
            continue
        if item.parent_id is None:
            continue
        parent = parents_by_id.get(item.parent_id)
        if parent is None:
            continue
        if parent.item_kind is ZaisanKind.SEASON:
            seasons[parent.id] = parent
        elif item.item_kind is ZaisanKind.SPECIAL and parent.item_kind is ZaisanKind.SERIES:
            series[parent.id] = parent
    series_parent_ids = {
        season.parent_id for season in seasons.values() if season.parent_id is not None
    }
    missing_parent_ids = series_parent_ids.difference(parents_by_id)
    if missing_parent_ids:
        parents_by_id.update(
            {
                parent.id: parent
                for parent in session.scalars(
                    select(Zaisan).where(Zaisan.id.in_(missing_parent_ids))
                )
            }
        )
    for parent_id in series_parent_ids:
        parent = parents_by_id.get(parent_id)
        if parent is not None and parent.item_kind is ZaisanKind.SERIES:
            series[parent.id] = parent
    return seasons, series


def _synchronise_season_completion(
    session: Session,
    *,
    user_id: int,
    season: Zaisan,
    completed_at: datetime | None,
) -> None:
    _synchronise_completion_state(
        session,
        user_id=user_id,
        item=season,
        completed=_children_are_completed(
            session,
            user_id=user_id,
            parent_id=season.id,
            child_kinds=EPISODIC_ITEM_KINDS,
        ),
        completed_at=completed_at,
    )


def _synchronise_series_completion(
    session: Session,
    *,
    user_id: int,
    series: Zaisan,
    completed_at: datetime | None,
) -> None:
    _synchronise_completion_state(
        session,
        user_id=user_id,
        item=series,
        completed=_children_are_completed(
            session,
            user_id=user_id,
            parent_id=series.id,
            child_kinds=_SERIES_COMPLETION_CHILD_KINDS,
        ),
        completed_at=completed_at,
    )


def _children_are_completed(
    session: Session,
    *,
    user_id: int,
    parent_id: int,
    child_kinds: frozenset[ZaisanKind],
) -> bool:
    child_count = session.scalar(
        select(func.count())
        .select_from(Zaisan)
        .where(Zaisan.parent_id == parent_id, Zaisan.item_kind.in_(child_kinds))
    )
    if child_count == 0:
        return False
    incomplete_child_count = session.scalar(
        select(func.count())
        .select_from(Zaisan)
        .outerjoin(
            PlaybackState,
            and_(
                PlaybackState.library_item_id == Zaisan.id,
                PlaybackState.user_id == user_id,
            ),
        )
        .where(
            Zaisan.parent_id == parent_id,
            Zaisan.item_kind.in_(child_kinds),
            or_(PlaybackState.id.is_(None), PlaybackState.completed.is_(False)),
        )
    )
    return incomplete_child_count == 0


def _synchronise_completion_state(
    session: Session,
    *,
    user_id: int,
    item: Zaisan,
    completed: bool,
    completed_at: datetime | None,
) -> None:
    state: PlaybackState | None = session.scalar(
        select(PlaybackState).where(
            PlaybackState.user_id == user_id,
            PlaybackState.library_item_id == item.id,
        )
    )
    if not completed:
        if state is not None:
            session.delete(state)
        return
    if state is not None:
        state.completed = True
        return
    session.add(
        PlaybackState(
            user_id=user_id,
            library_item_id=item.id,
            position_seconds=0.0,
            duration_seconds=0.0,
            completed=True,
            play_count=0,
            last_played_at=completed_at or datetime.now(UTC),
        )
    )


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


def allowed_parent_kinds(item_kind: ZaisanKind) -> frozenset[ZaisanKind] | None:
    """Return the valid parent kinds, or ``None`` for top-level kinds."""

    return _PARENT_KINDS.get(item_kind)


def validate_library_item_parent(
    session: Session,
    library_root_id: int,
    item_kind: ZaisanKind,
    parent_id: int | None,
) -> None:
    parent_kinds = allowed_parent_kinds(item_kind)
    if parent_id is None:
        if parent_kinds is not None:
            msg: LiteralString = f"{item_kind.value} items require a parent."
            raise ValueError(msg)
        return
    if parent_kinds is None:
        msg = f"{item_kind.value} items cannot have a parent."
        raise ValueError(msg)
    parent = _require_item(session, parent_id)
    if parent.library_root_id != library_root_id:
        msg = "A library item's parent must be in the same library root."
        raise ValueError(msg)
    if parent.item_kind not in parent_kinds:
        expected = ", ".join(kind.value for kind in sorted(parent_kinds))
        msg = f"{item_kind.value} requires one of these parent kinds: {expected}."
        raise ValueError(msg)


def normalise_library_item_tags(values: AbstractCollection[str]) -> list[str]:
    """Store ordinary item tags once, with stable casing and no blank values."""

    tags = {value.strip().casefold() for value in values}
    if "" in tags:
        msg = "Library item tags cannot be blank."
        raise ValueError(msg)
    return sorted(tags)


def _require_item(session: Session, library_item_id: int) -> Zaisan:
    item: Zaisan | None = session.get(Zaisan, library_item_id)
    if item is None:
        msg = f"Library item {library_item_id} does not exist."
        raise LookupError(msg)
    return item


def _require_text(value: str, description: str) -> str:
    normalised = value.strip()
    if not normalised:
        msg = f"{description} cannot be empty."
        raise ValueError(msg)
    return normalised

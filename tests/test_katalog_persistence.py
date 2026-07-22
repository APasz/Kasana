from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from kasana.katalog.database import KatalogDatabase
from kasana.katalog.models import (
    AvailabilityState,
    CollectionKin,
    KeiroEntry,
    KeiroKind,
    Kinship,
    Kura,
    MediaFile,
    PlaybackState,
    Zaisan,
    ZaisanKind,
)
from kasana.katalog.services import (
    add_collection_membership,
    append_watch_order_entry,
    attach_media_file,
    create_collection,
    create_library_item,
    create_library_root,
    create_user,
    create_watch_order,
    delete_library_item,
    record_playback_progress,
    set_media_file_availability,
)


@pytest.fixture
def database(tmp_path: Path) -> Generator[KatalogDatabase]:
    katalog_database = KatalogDatabase(tmp_path / "katalog.sqlite3")
    katalog_database.create_schema()
    yield katalog_database
    katalog_database.close()


def _create_root(database: KatalogDatabase, path: Path) -> int:
    def operation(session: Session) -> int:
        return create_library_root(
            session,
            path=path,
            expected_media_kind=ZaisanKind.MOVIE,
            default_tags=frozenset({"personal"}),
        ).id

    return database.run_transaction(operation)


def _create_movie(database: KatalogDatabase, root_id: int, title: str = "Stargate") -> int:
    return database.run_transaction(
        lambda session: (
            create_library_item(
                session,
                library_root_id=root_id,
                item_kind=ZaisanKind.MOVIE,
                title=title,
            ).id
        )
    )


def test_sqlite_connection_policy(database: KatalogDatabase) -> None:
    with database.engine.connect() as connection:
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1
        assert connection.scalar(text("PRAGMA journal_mode")) == "wal"
        assert connection.scalar(text("PRAGMA busy_timeout")) == 5_000


def test_hierarchy_integrity_and_playable_file_ownership(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    root_id = _create_root(database, tmp_path / "movies")

    def operation(session: Session) -> int:
        series = create_library_item(
            session,
            library_root_id=root_id,
            item_kind=ZaisanKind.SERIES,
            title="Stargate SG-1",
        )
        season = create_library_item(
            session,
            library_root_id=root_id,
            parent_id=series.id,
            item_kind=ZaisanKind.SEASON,
            title="Season 1",
            season_number=1,
        )
        episode = create_library_item(
            session,
            library_root_id=root_id,
            parent_id=season.id,
            item_kind=ZaisanKind.EPISODE,
            title="Children of the Gods",
            season_number=1,
            episode_number=1,
        )
        media_file = attach_media_file(
            session,
            library_item_id=episode.id,
            absolute_path=tmp_path / "media" / "sg1-s01e01.mkv",
            size_bytes=123,
            mtime_ns=456,
            container="matroska",
            duration_seconds=5_400.0,
        )
        with pytest.raises(ValueError, match="require a parent"):
            create_library_item(
                session,
                library_root_id=root_id,
                item_kind=ZaisanKind.EPISODE,
                title="Invalid episode",
                season_number=1,
                episode_number=2,
            )
        with pytest.raises(ValueError, match="cannot own playable"):
            attach_media_file(
                session,
                library_item_id=series.id,
                absolute_path=tmp_path / "media" / "series.mkv",
                size_bytes=1,
                mtime_ns=1,
                container="matroska",
            )
        return media_file.library_item_id

    assert database.run_transaction(operation) > 0


def test_collection_membership_and_mixed_watch_order(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    root_id = _create_root(database, tmp_path / "library")
    movie_id = _create_movie(database, root_id, "Stargate")

    def create_order(session: Session) -> tuple[int, int, int]:
        series = create_library_item(
            session,
            library_root_id=root_id,
            item_kind=ZaisanKind.SERIES,
            title="Stargate SG-1",
        )
        season = create_library_item(
            session,
            library_root_id=root_id,
            parent_id=series.id,
            item_kind=ZaisanKind.SEASON,
            title="Season 1",
            season_number=1,
        )
        episode = create_library_item(
            session,
            library_root_id=root_id,
            parent_id=season.id,
            item_kind=ZaisanKind.EPISODE,
            title="Children of the Gods",
            season_number=1,
            episode_number=1,
        )
        collection = create_collection(session, name="Stargate")
        membership = add_collection_membership(
            session,
            collection_id=collection.id,
            library_item_id=movie_id,
            relationship=Kinship.PRIMARY,
        )
        watch_order = create_watch_order(
            session,
            collection_id=collection.id,
            name="Recommended",
            order_kind=KeiroKind.RECOMMENDED,
        )
        first = append_watch_order_entry(
            session, watch_order_id=watch_order.id, library_item_id=movie_id
        )
        second = append_watch_order_entry(
            session, watch_order_id=watch_order.id, library_item_id=episode.id
        )
        assert membership.relationship is Kinship.PRIMARY
        assert (first.position, second.position) == (0, 1)
        with pytest.raises(ValueError, match="cannot appear"):
            append_watch_order_entry(
                session, watch_order_id=watch_order.id, library_item_id=season.id
            )
        return watch_order.id, movie_id, episode.id

    watch_order_id, _, episode_id = database.run_transaction(create_order)

    def read_order(session: Session) -> list[int]:
        entries = session.scalars(
            select(KeiroEntry)
            .where(KeiroEntry.watch_order_id == watch_order_id)
            .order_by(KeiroEntry.position)
        ).all()
        return [entry.library_item_id for entry in entries]

    assert database.run_transaction(read_order) == [movie_id, episode_id]


def test_uniqueness_constraints(database: KatalogDatabase, tmp_path: Path) -> None:
    root_path = tmp_path / "same-library"
    root_id = _create_root(database, root_path)
    _create_movie(database, root_id)

    with pytest.raises(IntegrityError):
        database.run_transaction(
            lambda session: create_library_root(
                session, path=root_path, expected_media_kind=ZaisanKind.MOVIE
            )
        )
    with pytest.raises(IntegrityError):
        database.run_transaction(
            lambda session: create_library_item(
                session,
                library_root_id=root_id,
                item_kind=ZaisanKind.MOVIE,
                title="Stargate",
            )
        )


def test_playback_state_updates(database: KatalogDatabase, tmp_path: Path) -> None:
    root_id = _create_root(database, tmp_path / "playback")
    movie_id = _create_movie(database, root_id)

    def record_twice(session: Session) -> tuple[int, bool, int]:
        user = create_user(session, username="sam")
        initial = record_playback_progress(
            session,
            user_id=user.id,
            library_item_id=movie_id,
            position_seconds=120.0,
            duration_seconds=6_000.0,
            completed=False,
            increment_play_count=True,
        )
        updated = record_playback_progress(
            session,
            user_id=user.id,
            library_item_id=movie_id,
            position_seconds=6_000.0,
            duration_seconds=6_000.0,
            completed=True,
            increment_play_count=True,
        )
        return initial.id, updated.completed, updated.play_count

    _, completed, play_count = database.run_transaction(record_twice)
    assert completed is True
    assert play_count == 2


def test_availability_updates_and_item_deletion_cascades(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    root_id = _create_root(database, tmp_path / "delete")
    movie_id = _create_movie(database, root_id)

    def create_dependants(session: Session) -> int:
        media_file = attach_media_file(
            session,
            library_item_id=movie_id,
            absolute_path=tmp_path / "delete" / "movie.mkv",
            size_bytes=100,
            mtime_ns=1,
            container="matroska",
        )
        set_media_file_availability(
            session, media_file_id=media_file.id, availability=AvailabilityState.MISSING
        )
        collection = create_collection(session, name="Delete test")
        add_collection_membership(session, collection_id=collection.id, library_item_id=movie_id)
        user = create_user(session, username="jack")
        record_playback_progress(
            session,
            user_id=user.id,
            library_item_id=movie_id,
            position_seconds=0.0,
            duration_seconds=100.0,
            completed=False,
        )
        return media_file.id

    media_file_id = database.run_transaction(create_dependants)

    def verify_and_delete(session: Session) -> None:
        persisted_file = session.get(MediaFile, media_file_id)
        assert persisted_file is not None
        assert persisted_file.availability is AvailabilityState.MISSING
        delete_library_item(session, library_item_id=movie_id)

    database.run_transaction(verify_and_delete)

    def verify_deletion(session: Session) -> tuple[Zaisan | None, MediaFile | None, int, int]:
        return (
            session.get(Zaisan, movie_id),
            session.get(MediaFile, media_file_id),
            session.scalar(select(func.count()).select_from(CollectionKin)) or 0,
            session.scalar(select(func.count()).select_from(PlaybackState)) or 0,
        )

    item, media_file, memberships, playback_states = database.run_transaction(verify_deletion)
    assert item is None
    assert media_file is None
    assert memberships == 0
    assert playback_states == 0


def test_transaction_rolls_back_on_error(database: KatalogDatabase, tmp_path: Path) -> None:
    def rollback(session: Session) -> None:
        create_library_root(
            session,
            path=tmp_path / "rollback",
            expected_media_kind=ZaisanKind.MOVIE,
        )
        msg = "rollback"
        raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="rollback"):
        database.run_transaction(rollback)

    assert (
        database.run_transaction(
            lambda session: session.scalar(select(func.count()).select_from(Kura))
        )
        == 0
    )


def test_folded_migration_creates_current_child_identity_indexes(tmp_path: Path) -> None:
    database_path = tmp_path / "katalog.sqlite3"
    repository_root = Path(__file__).parents[1]
    config = Config(str(repository_root / "alembic.ini"))
    config.set_main_option("script_location", str(repository_root / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")

    command.upgrade(config, "head")

    database = KatalogDatabase(database_path)
    try:
        with database.engine.connect() as connection:
            index_sql = connection.scalar(
                text(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'index' AND name = 'ix_library_item_child_identity'"
                )
            )
            episode_index_sql = connection.scalar(
                text(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'index' AND name = 'ix_library_item_episode_number'"
                )
            )
    finally:
        database.close()

    assert index_sql is not None
    assert "item_kind != 'episode'" in index_sql
    assert episode_index_sql is not None
    assert "episode_number IS NOT NULL" in episode_index_sql

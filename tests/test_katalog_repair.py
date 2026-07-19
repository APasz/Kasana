from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from kasana.katalog.database import KatalogDatabase
from kasana.katalog.models import (
    HierarchyRepairRun,
    KeiroKind,
    MediaFile,
    MetadataField,
    Zaisan,
    ZaisanKind,
)
from kasana.katalog.repair import (
    HierarchyRepairService,
    RepairActionKind,
    repair_backup_path,
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
    record_playback_progress,
)


@pytest.fixture
def database(tmp_path: Path) -> Generator[KatalogDatabase]:
    katalog_database = KatalogDatabase(tmp_path / "katalog.sqlite3")
    katalog_database.create_schema()
    yield katalog_database
    katalog_database.close()


def test_hierarchy_repair_renames_decade_pseudo_movie_without_changing_its_id(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    movies = tmp_path / "Movies"

    def seed(session: Session) -> int:
        root = create_library_root(session, path=movies, expected_media_kind=ZaisanKind.MOVIE)
        item = create_library_item(
            session, library_root_id=root.id, item_kind=ZaisanKind.MOVIE, title="00's"
        )
        attach_media_file(
            session,
            library_item_id=item.id,
            absolute_path=movies / "00's" / "Cars.mkv",
            size_bytes=1,
            mtime_ns=1,
            container="matroska",
        )
        return item.id

    item_id = database.run_transaction(seed)
    service = HierarchyRepairService(database)

    dry_run = service.dry_run()

    assert [action.kind for action in dry_run.plan.actions] == [RepairActionKind.RENAME]
    assert dry_run.applied is False
    backup = repair_backup_path(database.database_path)
    database.backup_to(backup)
    result = service.apply(backup_path=backup)

    assert result.applied is True
    assert backup.exists()

    def read(session: Session) -> tuple[int, str, int, int]:
        item = session.get(Zaisan, item_id)
        assert item is not None
        return (
            item.id,
            item.title,
            item.media_files[0].library_item_id,
            len(session.scalars(select(HierarchyRepairRun)).all()),
        )

    repaired_id, repaired_title, media_item_id, run_count = database.run_transaction(read)
    assert repaired_id == item_id
    assert repaired_title == "Cars"
    assert media_item_id == item_id
    assert run_count == 2


def test_hierarchy_repair_merge_preserves_playback_collections_and_watch_order_entries(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    movies = tmp_path / "Movies"

    def seed(session: Session) -> tuple[int, int]:
        root = create_library_root(session, path=movies, expected_media_kind=ZaisanKind.MOVIE)
        malformed = create_library_item(
            session, library_root_id=root.id, item_kind=ZaisanKind.MOVIE, title="00's"
        )
        cars = create_library_item(
            session, library_root_id=root.id, item_kind=ZaisanKind.MOVIE, title="Cars"
        )
        attach_media_file(
            session,
            library_item_id=malformed.id,
            absolute_path=movies / "00's" / "Cars.mkv",
            size_bytes=1,
            mtime_ns=1,
            container="matroska",
        )
        user = create_user(session, username="repair-user")
        record_playback_progress(
            session,
            user_id=user.id,
            library_item_id=malformed.id,
            position_seconds=30,
            duration_seconds=60,
            completed=False,
        )
        collection = create_collection(session, name="Repair collection")
        add_collection_membership(
            session,
            collection_id=collection.id,
            library_item_id=malformed.id,
        )
        order = create_watch_order(
            session,
            collection_id=collection.id,
            name="Repair order",
            order_kind=KeiroKind.CUSTOM,
        )
        append_watch_order_entry(
            session,
            watch_order_id=order.id,
            library_item_id=malformed.id,
        )
        return malformed.id, cars.id

    malformed_id, cars_id = database.run_transaction(seed)
    service = HierarchyRepairService(database)
    plan = service.preview()

    assert {action.kind for action in plan.actions} == {
        RepairActionKind.MERGE,
        RepairActionKind.REASSIGN_MEDIA,
        RepairActionKind.REMOVE,
    }
    assert plan.impact.playback_states == 1
    assert plan.impact.collection_memberships == 1
    assert plan.impact.watch_order_entries == 1
    backup = repair_backup_path(database.database_path)
    database.backup_to(backup)
    service.apply(backup_path=backup)

    def references(session: Session) -> tuple[Zaisan | None, tuple[int, int, int, int]]:
        target = session.get(Zaisan, cars_id)
        assert target is not None
        return (
            session.get(Zaisan, malformed_id),
            (
                len(target.media_files),
                len(target.playback_states),
                len(target.collection_memberships),
                len(target.watch_order_entries),
            ),
        )

    removed, counts = database.run_transaction(references)
    assert removed is None
    assert counts == (1, 1, 1, 1)


def test_hierarchy_repair_creates_series_context_for_episode_catalogued_as_movie(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    shows = tmp_path / "TVShows"

    def seed(session: Session) -> int:
        root = create_library_root(session, path=shows, expected_media_kind=ZaisanKind.SERIES)
        malformed = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="S01E01",
        )
        session.add(
            MediaFile(
                library_item_id=malformed.id,
                absolute_path=str(shows / "Show Name" / "Season 01" / "S01E01.mkv"),
                size_bytes=1,
                mtime_ns=1,
                container="matroska",
                video_streams=[],
                attached_pictures=[],
                audio_streams=[],
                subtitle_streams=[],
            )
        )
        return malformed.id

    malformed_id = database.run_transaction(seed)
    backup = repair_backup_path(database.database_path)
    database.backup_to(backup)
    HierarchyRepairService(database).apply(backup_path=backup)

    def hierarchy(session: Session) -> tuple[Zaisan, Zaisan, Zaisan]:
        episode = session.get(Zaisan, malformed_id)
        assert episode is not None and episode.parent_id is not None
        season = session.get(Zaisan, episode.parent_id)
        assert season is not None and season.parent_id is not None
        series = session.get(Zaisan, season.parent_id)
        assert series is not None
        return episode, season, series

    episode, season, series = database.run_transaction(hierarchy)
    assert (episode.item_kind, episode.season_number, episode.episode_number) == (
        ZaisanKind.EPISODE,
        1,
        1,
    )
    assert (season.item_kind, season.season_number, series.item_kind, series.title) == (
        ZaisanKind.SEASON,
        1,
        ZaisanKind.SERIES,
        "Show Name",
    )


def test_hierarchy_repair_leaves_title_locked_container_for_manual_review(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    movies = tmp_path / "Movies"

    def seed(session: Session) -> None:
        root = create_library_root(session, path=movies, expected_media_kind=ZaisanKind.MOVIE)
        malformed = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="00's",
            locked_metadata_fields=frozenset({MetadataField.TITLE}),
        )
        create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="Cars",
        )
        attach_media_file(
            session,
            library_item_id=malformed.id,
            absolute_path=movies / "00's" / "Cars.mkv",
            size_bytes=1,
            mtime_ns=1,
            container="matroska",
        )

    database.run_transaction(seed)

    plan = HierarchyRepairService(database).preview()

    assert plan.actions == ()
    assert len(plan.manual_reviews) == 1
    assert "manually locked" in plan.manual_reviews[0].reason


def test_hierarchy_repair_reparents_extras_specials_and_orphan_season_branches(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    movies = tmp_path / "Movies"
    shows = tmp_path / "TVShows"

    def seed(session: Session) -> dict[str, int]:
        movie_root = create_library_root(session, path=movies, expected_media_kind=ZaisanKind.MOVIE)
        show_root = create_library_root(session, path=shows, expected_media_kind=ZaisanKind.SERIES)
        movie_extra = Zaisan(
            library_root_id=movie_root.id,
            item_kind=ZaisanKind.EXTRA,
            title="Trailer",
            sort_title="Trailer",
        )
        session.add(movie_extra)
        session.flush()
        attach_media_file(
            session,
            library_item_id=movie_extra.id,
            absolute_path=movies / "Feature" / "Extras" / "Trailer.mkv",
            size_bytes=1,
            mtime_ns=1,
            container="matroska",
        )
        special_as_movie = create_library_item(
            session,
            library_root_id=show_root.id,
            item_kind=ZaisanKind.MOVIE,
            title="Bonus",
        )
        attach_media_file(
            session,
            library_item_id=special_as_movie.id,
            absolute_path=shows / "Show Name" / "Season 00" / "Bonus.mkv",
            size_bytes=1,
            mtime_ns=1,
            container="matroska",
        )
        orphan_season = Zaisan(
            library_root_id=show_root.id,
            item_kind=ZaisanKind.SEASON,
            title="Season 2",
            sort_title="Season 2",
            season_number=2,
        )
        session.add(orphan_season)
        session.flush()
        orphan_episode = Zaisan(
            library_root_id=show_root.id,
            parent_id=orphan_season.id,
            item_kind=ZaisanKind.EPISODE,
            title="Episode 1",
            sort_title="Episode 1",
            season_number=2,
            episode_number=1,
        )
        session.add(orphan_episode)
        session.flush()
        attach_media_file(
            session,
            library_item_id=orphan_episode.id,
            absolute_path=shows / "Orphan Show" / "Season 02" / "S02E01.mkv",
            size_bytes=1,
            mtime_ns=1,
            container="matroska",
        )
        episode_as_movie = Zaisan(
            library_root_id=movie_root.id,
            item_kind=ZaisanKind.EPISODE,
            title="Wrong episode",
            sort_title="Wrong episode",
            season_number=1,
            episode_number=1,
        )
        session.add(episode_as_movie)
        session.flush()
        attach_media_file(
            session,
            library_item_id=episode_as_movie.id,
            absolute_path=movies / "Standalone Film.mkv",
            size_bytes=1,
            mtime_ns=1,
            container="matroska",
        )
        create_library_item(
            session,
            library_root_id=show_root.id,
            item_kind=ZaisanKind.SERIES,
            title="The Office",
        )
        create_library_item(
            session,
            library_root_id=show_root.id,
            item_kind=ZaisanKind.SERIES,
            title="Office",
        )
        multiple_movies = create_library_item(
            session,
            library_root_id=movie_root.id,
            item_kind=ZaisanKind.MOVIE,
            title="Compilation",
        )
        for filename in ("First Film.mkv", "Second Film.mkv"):
            attach_media_file(
                session,
                library_item_id=multiple_movies.id,
                absolute_path=movies / filename,
                size_bytes=1,
                mtime_ns=1,
                container="matroska",
            )
        return {
            "movie_extra": movie_extra.id,
            "special": special_as_movie.id,
            "season": orphan_season.id,
            "episode": orphan_episode.id,
            "episode_as_movie": episode_as_movie.id,
        }

    identifiers = database.run_transaction(seed)
    service = HierarchyRepairService(database)
    plan = service.preview()

    assert {"duplicate_series_minor_variation", "multiple_unrelated_movie_media"} <= {
        review.reason.split("]", maxsplit=1)[0].removeprefix("[") for review in plan.manual_reviews
    }
    assert {RepairActionKind.CREATE, RepairActionKind.REPARENT, RepairActionKind.RETYPE} <= {
        action.kind for action in plan.actions
    }
    backup = repair_backup_path(database.database_path)
    database.backup_to(backup)
    service.apply(backup_path=backup)

    def repaired(
        session: Session,
    ) -> tuple[
        tuple[ZaisanKind, str],
        tuple[ZaisanKind, ZaisanKind, str],
        tuple[ZaisanKind, str, int],
        tuple[ZaisanKind, int | None, str],
    ]:
        items = tuple(session.scalars(select(Zaisan)).all())
        by_id = {item.id: item for item in items}
        movie_extra = by_id[identifiers["movie_extra"]]
        special = by_id[identifiers["special"]]
        season = by_id[identifiers["season"]]
        episode = by_id[identifiers["episode"]]
        episode_as_movie = by_id[identifiers["episode_as_movie"]]
        assert movie_extra.parent_id is not None
        assert special.parent_id is not None
        assert season.parent_id is not None
        assert episode.parent_id is not None
        movie_parent = by_id[movie_extra.parent_id]
        special_parent = by_id[special.parent_id]
        season_parent = by_id[season.parent_id]
        return (
            (movie_parent.item_kind, movie_parent.title),
            (special.item_kind, special_parent.item_kind, special_parent.title),
            (season_parent.item_kind, season_parent.title, episode.parent_id),
            (episode_as_movie.item_kind, episode_as_movie.parent_id, episode_as_movie.title),
        )

    movie_parent, special_status, season_status, movie_status = database.run_transaction(repaired)
    assert movie_parent == (ZaisanKind.MOVIE, "Feature")
    assert special_status == (
        ZaisanKind.SPECIAL,
        ZaisanKind.SERIES,
        "Show Name",
    )
    assert season_status == (
        ZaisanKind.SERIES,
        "Orphan Show",
        identifiers["season"],
    )
    assert movie_status == (
        ZaisanKind.MOVIE,
        None,
        "Standalone Film",
    )

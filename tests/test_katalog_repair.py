from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Thread

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from kasana.katalog.api.service import KatalogQueryService
from kasana.katalog.database import KatalogDatabase
from kasana.katalog.limits import MAX_LIBRARY_ITEM_EXTERNAL_IDENTIFIERS
from kasana.katalog.models import (
    AvailabilityState,
    Collection,
    CollectionKin,
    HierarchyRepairRun,
    JSONObject,
    Keiro,
    KeiroEntry,
    KeiroKind,
    MediaFile,
    MetadataBinding,
    MetadataCandidate,
    MetadataCandidateStatus,
    MetadataField,
    MetadataMatchStatus,
    PlaybackContextKind,
    PlaybackSession,
    PlaybackSessionEntry,
    User,
    Zaisan,
    ZaisanKind,
)
from kasana.katalog.parsing import LibraryLayout
from kasana.katalog.repair import (
    DuplicateResolutionService,
    HierarchyRepairFilters,
    HierarchyRepairPlan,
    HierarchyRepairService,
    ManualItemMergeError,
    ManualItemMergeField,
    ManualItemMergeFieldChoice,
    ManualItemMergeService,
    ManualItemMergeSide,
    RepairActionKind,
    _build_plan,  # pyright: ignore[reportPrivateUsage]
    _merged_local_external_ids,  # pyright: ignore[reportPrivateUsage]
    duplicate_resolution_backup_path,
    manual_item_merge_backup_path,
    repair_backup_path,
)
from kasana.katalog.series_paths import accepted_series_path_aliases, series_title_identity
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


def test_hierarchy_repair_serialises_writers_before_building_its_plan(
    database: KatalogDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = HierarchyRepairService(database)
    backup = repair_backup_path(database.database_path)
    database.backup_to(backup)
    planning_started = Event()
    release_plan = Event()
    competing_writer_finished = Event()
    errors: list[Exception] = []

    def paused_build_plan(
        session: Session, filters: HierarchyRepairFilters
    ) -> HierarchyRepairPlan:
        planning_started.set()
        assert release_plan.wait(timeout=2)
        return original_build_plan(session, filters)

    original_build_plan = _build_plan
    monkeypatch.setattr("kasana.katalog.repair._build_plan", paused_build_plan)

    def apply_repair() -> None:
        try:
            service.apply(backup_path=backup)
        except Exception as error:
            errors.append(error)

    def start_competing_writer() -> None:
        try:
            database.run_transaction(lambda _session: None, immediate=True)
        except Exception as error:
            errors.append(error)
        finally:
            competing_writer_finished.set()

    repair_thread = Thread(target=apply_repair)
    writer_thread = Thread(target=start_competing_writer)
    try:
        repair_thread.start()
        assert planning_started.wait(timeout=2)
        writer_thread.start()
        assert not competing_writer_finished.wait(timeout=0.1)
    finally:
        release_plan.set()
        repair_thread.join(timeout=2)
        writer_thread.join(timeout=2)

    assert not repair_thread.is_alive()
    assert not writer_thread.is_alive()
    assert errors == []
    assert competing_writer_finished.is_set()


def test_merged_local_external_ids_respect_the_public_limit(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    movies = tmp_path / "Movies"

    def merge(session: Session) -> list[JSONObject]:
        root = create_library_root(session, path=movies, expected_media_kind=ZaisanKind.MOVIE)
        source = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="Source",
        )
        target = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="Target",
        )
        source.local_external_ids = [
            {"namespace": "source", "value": str(index)}
            for index in range(MAX_LIBRARY_ITEM_EXTERNAL_IDENTIFIERS)
        ]
        target.local_external_ids = [
            {"namespace": "target", "value": str(index)}
            for index in range(MAX_LIBRARY_ITEM_EXTERNAL_IDENTIFIERS)
        ]
        return _merged_local_external_ids(source, target)

    assert database.run_transaction(merge) == [
        {"namespace": "source", "value": str(index)}
        for index in range(MAX_LIBRARY_ITEM_EXTERNAL_IDENTIFIERS)
    ]


def test_hierarchy_repair_merge_preserves_playback_collections_and_watch_order_entries(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    movies = tmp_path / "Movies"

    def seed(session: Session) -> tuple[int, int]:
        root = create_library_root(session, path=movies, expected_media_kind=ZaisanKind.MOVIE)
        malformed = create_library_item(
            session, library_root_id=root.id, item_kind=ZaisanKind.MOVIE, title="00's"
        )
        malformed.local_external_ids = [{"namespace": "imdb", "value": "tt1234567"}]
        cars = create_library_item(
            session, library_root_id=root.id, item_kind=ZaisanKind.MOVIE, title="Cars"
        )
        cars.local_external_ids = [{"namespace": "tmdb", "value": "920"}]
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

    def local_external_ids(session: Session) -> list[JSONObject]:
        target = session.get(Zaisan, cars_id)
        assert target is not None
        return target.local_external_ids

    assert database.run_transaction(local_external_ids) == [
        {"namespace": "imdb", "value": "tt1234567"},
        {"namespace": "tmdb", "value": "920"},
    ]


def test_duplicate_resolution_merges_unambiguous_media_less_movie_into_file_backed_item(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    movies = tmp_path / "Movies"

    def seed(session: Session) -> tuple[int, int]:
        root = create_library_root(session, path=movies, expected_media_kind=ZaisanKind.MOVIE)
        source = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="Everything Everywhere All at Once",
            release_year=2022,
        )
        source.overview = "A family crosses the multiverse."
        source.tags = ["science fiction"]
        target = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="Everything, Everywhere, All At Once",
            release_year=2022,
        )
        attach_media_file(
            session,
            library_item_id=target.id,
            absolute_path=movies / "Everything Everywhere All at Once (2022).mkv",
            size_bytes=1,
            mtime_ns=1,
            container="matroska",
        )
        session.add(
            MetadataBinding(
                library_item_id=source.id,
                provider="tmdb",
                provider_id="545611",
                provider_media_kind=ZaisanKind.MOVIE,
                status=MetadataMatchStatus.MATCHED,
                scoring_explanation=[],
                provider_external_ids=[],
            )
        )
        session.add(
            MetadataCandidate(
                library_item_id=target.id,
                provider="tmdb",
                provider_id="545611",
                provider_media_kind=ZaisanKind.MOVIE,
                provider_title="Everything Everywhere All at Once",
                confidence=1.0,
                scoring_explanation=[],
                status=MetadataCandidateStatus.SUGGESTED,
                last_seen_at=datetime.now(UTC),
            )
        )
        user = create_user(session, username="duplicate-resolution-user")
        record_playback_progress(
            session,
            user_id=user.id,
            library_item_id=source.id,
            position_seconds=30,
            duration_seconds=60,
            completed=False,
        )
        collection = create_collection(session, name="Duplicate resolution collection")
        add_collection_membership(
            session,
            collection_id=collection.id,
            library_item_id=source.id,
        )
        order = create_watch_order(
            session,
            collection_id=collection.id,
            name="Duplicate resolution order",
            order_kind=KeiroKind.CUSTOM,
        )
        append_watch_order_entry(
            session,
            watch_order_id=order.id,
            library_item_id=source.id,
        )
        return source.id, target.id

    source_id, target_id = database.run_transaction(seed)
    service = DuplicateResolutionService(database)

    candidates = service.preview()
    assert len(candidates) == 1
    candidate = candidates[0]
    assert (candidate.source_item_id, candidate.target_item_id) == (source_id, target_id)
    assert candidate.impact.model_dump() == {
        "playback_states": 1,
        "metadata_bindings": 1,
        "collection_memberships": 1,
        "watch_order_entries": 1,
    }
    backup = duplicate_resolution_backup_path(database.database_path)
    database.backup_to(backup)
    service.apply(source_item_id=source_id, target_item_id=target_id, backup_path=backup)

    def resolved(
        session: Session,
    ) -> tuple[Zaisan | None, Zaisan, int, int, int, int, int, MetadataCandidateStatus]:
        target = session.get(Zaisan, target_id)
        assert target is not None
        return (
            session.get(Zaisan, source_id),
            target,
            len(target.media_files),
            len(target.playback_states),
            len(target.collection_memberships),
            len(target.watch_order_entries),
            len(target.metadata_bindings),
            target.metadata_candidates[0].status,
        )

    (
        removed,
        target,
        media_count,
        playback_count,
        membership_count,
        entry_count,
        binding_count,
        candidate_status,
    ) = database.run_transaction(resolved)
    assert backup.is_file()
    assert removed is None
    assert target.title == "Everything Everywhere All at Once"
    assert target.tags == ["science fiction"]
    assert target.overview == "A family crosses the multiverse."
    assert candidate_status is MetadataCandidateStatus.ACCEPTED
    assert (media_count, playback_count, membership_count, entry_count, binding_count) == (
        1,
        1,
        1,
        1,
        1,
    )


def test_manual_item_merge_keeps_selected_fields_and_retains_series_branches(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    series_root = tmp_path / "Series"

    def seed(session: Session) -> tuple[int, int, int, int, int, int, int, int]:
        root = create_library_root(session, path=series_root, expected_media_kind=ZaisanKind.SERIES)
        source = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.SERIES,
            title="The Lord of the Rings: The Rings of Power",
            release_year=2022,
        )
        source.overview = "Source overview"
        source.tags = ["fantasy", "source"]
        source.locked_metadata_fields = [MetadataField.TITLE.value]
        source.local_external_ids = [{"namespace": "tmdb", "value": "84773"}]
        target = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.SERIES,
            title="Rings of Power",
            release_year=2022,
        )
        target.overview = "Target overview"
        target.tags = ["favourite", "fantasy"]
        session.add_all(
            (
                MetadataBinding(
                    library_item_id=source.id,
                    provider="tmdb",
                    provider_id="84773",
                    provider_media_kind=ZaisanKind.SERIES,
                    status=MetadataMatchStatus.MATCHED,
                    scoring_explanation=[],
                    provider_external_ids=[],
                ),
                MetadataBinding(
                    library_item_id=target.id,
                    provider="tmdb",
                    provider_id="84773",
                    provider_media_kind=ZaisanKind.SERIES,
                    status=MetadataMatchStatus.UNMATCHED,
                    scoring_explanation=[],
                    provider_external_ids=[],
                ),
                MetadataCandidate(
                    library_item_id=target.id,
                    provider="tmdb",
                    provider_id="84773",
                    provider_media_kind=ZaisanKind.SERIES,
                    provider_title="The Lord of the Rings: The Rings of Power",
                    confidence=1.0,
                    scoring_explanation=[],
                    status=MetadataCandidateStatus.SUGGESTED,
                    last_seen_at=datetime.now(UTC),
                ),
            )
        )
        source_season = create_library_item(
            session,
            library_root_id=root.id,
            parent_id=source.id,
            item_kind=ZaisanKind.SEASON,
            title="Season One",
            season_number=1,
        )
        target_season = create_library_item(
            session,
            library_root_id=root.id,
            parent_id=target.id,
            item_kind=ZaisanKind.SEASON,
            title="Season 1",
            season_number=1,
        )
        source_episode = create_library_item(
            session,
            library_root_id=root.id,
            parent_id=source_season.id,
            item_kind=ZaisanKind.EPISODE,
            title="A Shadow of the Past",
            season_number=1,
            episode_number=1,
        )
        target_episode = create_library_item(
            session,
            library_root_id=root.id,
            parent_id=target_season.id,
            item_kind=ZaisanKind.EPISODE,
            title="Episode 1",
            season_number=1,
            episode_number=1,
        )
        source_only_episode = create_library_item(
            session,
            library_root_id=root.id,
            parent_id=source_season.id,
            item_kind=ZaisanKind.EPISODE,
            title="Adrift",
            season_number=1,
            episode_number=2,
        )
        attach_media_file(
            session,
            library_item_id=source_episode.id,
            absolute_path=series_root / "source-s01e01.mkv",
            size_bytes=1,
            mtime_ns=1,
            container="matroska",
        )
        attach_media_file(
            session,
            library_item_id=source_episode.id,
            absolute_path=series_root / "source-s01e01-alternate.mkv",
            size_bytes=1,
            mtime_ns=1,
            container="matroska",
        )
        attach_media_file(
            session,
            library_item_id=target_episode.id,
            absolute_path=series_root / "target-s01e01.mkv",
            size_bytes=1,
            mtime_ns=1,
            container="matroska",
        )
        attach_media_file(
            session,
            library_item_id=source_only_episode.id,
            absolute_path=series_root / "source-s01e02.mkv",
            size_bytes=1,
            mtime_ns=1,
            container="matroska",
        )
        collection = create_collection(session, name="The Second Age")
        add_collection_membership(
            session,
            collection_id=collection.id,
            library_item_id=source.id,
        )
        collection.artwork_item_id = source.id
        watch_order = create_watch_order(
            session,
            collection_id=collection.id,
            name="Release order",
            order_kind=KeiroKind.CUSTOM,
        )
        append_watch_order_entry(
            session,
            watch_order_id=watch_order.id,
            library_item_id=source_episode.id,
        )
        return (
            source.id,
            target.id,
            source_season.id,
            target_season.id,
            source_only_episode.id,
            target_episode.id,
            collection.id,
            watch_order.id,
        )

    (
        source_id,
        target_id,
        source_season_id,
        target_season_id,
        source_only_episode_id,
        target_episode_id,
        collection_id,
        watch_order_id,
    ) = database.run_transaction(seed)
    service = ManualItemMergeService(database)

    preview = service.preview(source_item_id=source_id, target_item_id=target_id)

    assert preview.matched_descendant_count == 2
    assert preview.transferred_descendant_count == 1
    assert {conflict.field for conflict in preview.conflicts} == {
        ManualItemMergeField.TITLE,
        ManualItemMergeField.SORT_TITLE,
        ManualItemMergeField.OVERVIEW,
    }
    backup = manual_item_merge_backup_path(database.database_path)
    database.backup_to(backup)
    service.apply(
        source_item_id=source_id,
        target_item_id=target_id,
        preview_token=preview.preview_token,
        field_choices=tuple(
            ManualItemMergeFieldChoice(
                field=conflict.field,
                keep=(
                    ManualItemMergeSide.SOURCE
                    if conflict.field is ManualItemMergeField.TITLE
                    else ManualItemMergeSide.TARGET
                ),
            )
            for conflict in preview.conflicts
        ),
        backup_path=backup,
    )

    def resolved(
        session: Session,
    ) -> tuple[
        Zaisan | None,
        Zaisan | None,
        Zaisan,
        Zaisan,
        int,
        int,
        int,
        int,
        int,
        MetadataMatchStatus,
        MetadataCandidateStatus,
    ]:
        target = session.get(Zaisan, target_id)
        retained_episode = session.get(Zaisan, source_only_episode_id)
        collection = session.get(Collection, collection_id)
        watch_order = session.get(Keiro, watch_order_id)
        assert target is not None and collection is not None and watch_order is not None
        assert retained_episode is not None
        assert collection.artwork_item_id is not None
        assert len(target.metadata_bindings) == 1
        assert len(target.metadata_candidates) == 1
        watch_order_item_id = session.scalar(
            select(KeiroEntry.library_item_id).where(KeiroEntry.watch_order_id == watch_order.id)
        )
        assert watch_order_item_id is not None
        return (
            session.get(Zaisan, source_id),
            session.get(Zaisan, source_season_id),
            target,
            retained_episode,
            len(session.scalars(select(MediaFile)).all()),
            collection.artwork_item_id,
            collection.revision,
            watch_order.revision,
            watch_order_item_id,
            target.metadata_bindings[0].status,
            target.metadata_candidates[0].status,
        )

    (
        removed_source,
        removed_season,
        target,
        retained_episode,
        media_count,
        collection_artwork_item_id,
        collection_revision,
        watch_order_revision,
        watch_order_item_id,
        binding_status,
        candidate_status,
    ) = database.run_transaction(resolved)

    assert backup.is_file()
    assert removed_source is None
    assert removed_season is None
    assert target.title == "The Lord of the Rings: The Rings of Power"
    assert target.overview == "Target overview"
    assert target.tags == ["fantasy", "favourite", "source"]
    assert target.locked_metadata_fields == [MetadataField.TITLE.value]
    assert target.local_external_ids == [{"namespace": "tmdb", "value": "84773"}]
    assert retained_episode.parent_id == target_season_id
    assert media_count == 4
    assert collection_artwork_item_id == target_id
    assert collection_revision == 2
    assert watch_order_revision == 2
    assert watch_order_item_id == target_episode_id
    assert binding_status is MetadataMatchStatus.MATCHED
    assert candidate_status is MetadataCandidateStatus.ACCEPTED


def test_item_deletion_advances_dependent_state_revisions(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    shows = tmp_path / "Shows"
    playback_session_id = "deletion-state-session"

    def seed(session: Session) -> tuple[int, int, int, int, int, int]:
        root = create_library_root(session, path=shows, expected_media_kind=ZaisanKind.SERIES)
        series = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.SERIES,
            title="Example series",
        )
        season = create_library_item(
            session,
            library_root_id=root.id,
            parent_id=series.id,
            item_kind=ZaisanKind.SEASON,
            title="Season 1",
            season_number=1,
        )
        episode = create_library_item(
            session,
            library_root_id=root.id,
            parent_id=season.id,
            item_kind=ZaisanKind.EPISODE,
            title="Pilot",
            season_number=1,
            episode_number=1,
        )
        user = create_user(session, username="deletion-state-user")
        record_playback_progress(
            session,
            user_id=user.id,
            library_item_id=episode.id,
            position_seconds=30,
            duration_seconds=60,
            completed=False,
        )
        media_file = attach_media_file(
            session,
            library_item_id=episode.id,
            absolute_path=shows / "Example series" / "Season 01" / "S01E01.mkv",
            size_bytes=1,
            mtime_ns=1,
            container="matroska",
        )
        now = datetime.now(UTC)
        session.add(
            PlaybackSession(
                id=playback_session_id,
                user_id=user.id,
                context_kind=PlaybackContextKind.SERIES,
                context_item_id=series.id,
                current_entry_position=0,
                created_at=now,
                expires_at=now + timedelta(hours=1),
            )
        )
        session.add(
            PlaybackSessionEntry(
                playback_session_id=playback_session_id,
                position=0,
                library_item_id=episode.id,
                media_file_id=media_file.id,
            )
        )
        collection = create_collection(session, name="Deletion state collection")
        collection.artwork_item_id = season.id
        add_collection_membership(
            session,
            collection_id=collection.id,
            library_item_id=episode.id,
        )
        watch_order = create_watch_order(
            session,
            collection_id=collection.id,
            name="Episode order",
            order_kind=KeiroKind.CUSTOM,
        )
        append_watch_order_entry(
            session,
            watch_order_id=watch_order.id,
            library_item_id=episode.id,
        )
        return (
            series.id,
            collection.id,
            watch_order.id,
            user.id,
            collection.revision,
            watch_order.revision,
        )

    (
        series_id,
        collection_id,
        watch_order_id,
        user_id,
        collection_revision,
        watch_order_revision,
    ) = database.run_transaction(seed)

    def user_revision(session: Session) -> int:
        user = session.get(User, user_id)
        assert user is not None
        return user.playback_state_revision

    initial_user_revision = database.run_transaction(user_revision)

    KatalogQueryService(database, artwork_cache_path=tmp_path / "artwork").delete_item(
        series_id, confirm=True
    )

    def deleted_state(session: Session) -> tuple[int, int, int | None, int, int, int, bool, int]:
        collection = session.get(Collection, collection_id)
        watch_order = session.get(Keiro, watch_order_id)
        user = session.get(User, user_id)
        playback_session = session.get(PlaybackSession, playback_session_id)
        assert (
            collection is not None
            and watch_order is not None
            and user is not None
            and playback_session is not None
        )
        return (
            collection.revision,
            watch_order.revision,
            collection.artwork_item_id,
            len(session.scalars(select(CollectionKin)).all()),
            len(session.scalars(select(KeiroEntry)).all()),
            user.playback_state_revision,
            playback_session.closed_at is not None,
            len(
                session.scalars(
                    select(PlaybackSessionEntry).where(
                        PlaybackSessionEntry.playback_session_id == playback_session_id
                    )
                ).all()
            ),
        )

    (
        current_collection_revision,
        current_watch_order_revision,
        artwork_item_id,
        membership_count,
        entry_count,
        current_user_revision,
        playback_session_closed,
        playback_session_entry_count,
    ) = database.run_transaction(deleted_state)

    assert current_collection_revision == collection_revision + 1
    assert current_watch_order_revision == watch_order_revision + 1
    assert artwork_item_id is None
    assert membership_count == 0
    assert entry_count == 0
    assert current_user_revision == initial_user_revision + 1
    assert playback_session_closed is True
    assert playback_session_entry_count == 0


def test_manual_item_merge_rejects_ambiguous_removed_hierarchy(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    series_root = tmp_path / "Series"

    def seed(session: Session) -> tuple[int, int]:
        root = create_library_root(session, path=series_root, expected_media_kind=ZaisanKind.SERIES)
        source = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.SERIES,
            title="Removed show",
        )
        target = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.SERIES,
            title="Kept show",
        )
        for title in ("First source season", "Second source season"):
            create_library_item(
                session,
                library_root_id=root.id,
                parent_id=source.id,
                item_kind=ZaisanKind.SEASON,
                title=title,
                season_number=1,
            )
        create_library_item(
            session,
            library_root_id=root.id,
            parent_id=target.id,
            item_kind=ZaisanKind.SEASON,
            title="Kept season",
            season_number=1,
        )
        return source.id, target.id

    source_id, target_id = database.run_transaction(seed)

    with pytest.raises(ManualItemMergeError, match="removed item's hierarchy has ambiguous"):
        ManualItemMergeService(database).preview(
            source_item_id=source_id,
            target_item_id=target_id,
        )


def test_manual_item_merge_rejects_mismatched_direct_child_identity(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    series_root = tmp_path / "Series"

    def seed(session: Session) -> tuple[int, int]:
        root = create_library_root(session, path=series_root, expected_media_kind=ZaisanKind.SERIES)
        series = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.SERIES,
            title="Example series",
        )
        source = create_library_item(
            session,
            library_root_id=root.id,
            parent_id=series.id,
            item_kind=ZaisanKind.SEASON,
            title="Season 1",
            season_number=1,
        )
        target = create_library_item(
            session,
            library_root_id=root.id,
            parent_id=series.id,
            item_kind=ZaisanKind.SEASON,
            title="Season 2",
            season_number=2,
        )
        return source.id, target.id

    source_id, target_id = database.run_transaction(seed)

    with pytest.raises(ManualItemMergeError, match="matching season or episode identifiers"):
        ManualItemMergeService(database).preview(
            source_item_id=source_id,
            target_item_id=target_id,
        )


def test_manual_item_merge_rejects_conflicting_episode_ranges(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    series_root = tmp_path / "Series"

    def seed(session: Session) -> tuple[int, int]:
        root = create_library_root(session, path=series_root, expected_media_kind=ZaisanKind.SERIES)
        source = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.SERIES,
            title="Source series",
        )
        target = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.SERIES,
            title="Target series",
        )
        source_season = create_library_item(
            session,
            library_root_id=root.id,
            parent_id=source.id,
            item_kind=ZaisanKind.SEASON,
            title="Season 1",
            season_number=1,
        )
        target_season = create_library_item(
            session,
            library_root_id=root.id,
            parent_id=target.id,
            item_kind=ZaisanKind.SEASON,
            title="Season 1",
            season_number=1,
        )
        combined_episode = create_library_item(
            session,
            library_root_id=root.id,
            parent_id=source_season.id,
            item_kind=ZaisanKind.EPISODE,
            title="Combined premiere",
            season_number=1,
            episode_number=1,
        )
        combined_episode.episode_end_season_number = 1
        combined_episode.episode_end_number = 2
        create_library_item(
            session,
            library_root_id=root.id,
            parent_id=target_season.id,
            item_kind=ZaisanKind.EPISODE,
            title="Premiere",
            season_number=1,
            episode_number=1,
        )
        return source.id, target.id

    source_id, target_id = database.run_transaction(seed)

    with pytest.raises(ManualItemMergeError, match="source-only child would conflict"):
        ManualItemMergeService(database).preview(
            source_item_id=source_id,
            target_item_id=target_id,
        )


def test_manual_item_merge_preserves_a_matched_series_directory_alias(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    shows = tmp_path / "TVShows"
    directory_title = "LOTR; The Rings of Power"
    canonical_title = "The Lord of the Rings: The Rings of Power"

    def seed(session: Session) -> tuple[int, int, int, int]:
        root = create_library_root(session, path=shows, expected_media_kind=ZaisanKind.SERIES)
        source = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.SERIES,
            title=directory_title,
        )
        target = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.SERIES,
            title=canonical_title,
        )
        source_season = create_library_item(
            session,
            library_root_id=root.id,
            parent_id=source.id,
            item_kind=ZaisanKind.SEASON,
            title="Season 1",
            season_number=1,
        )
        target_season = create_library_item(
            session,
            library_root_id=root.id,
            parent_id=target.id,
            item_kind=ZaisanKind.SEASON,
            title="Season 1",
            season_number=1,
        )
        episode = create_library_item(
            session,
            library_root_id=root.id,
            parent_id=source_season.id,
            item_kind=ZaisanKind.EPISODE,
            title="A Shadow of the Past",
            season_number=1,
            episode_number=1,
        )
        attach_media_file(
            session,
            library_item_id=episode.id,
            absolute_path=shows / directory_title / "Season 01" / "S01E01.mkv",
            size_bytes=1,
            mtime_ns=1,
            container="matroska",
        )
        session.add(
            MetadataBinding(
                library_item_id=target.id,
                provider="tmdb",
                provider_id="84773",
                provider_media_kind=ZaisanKind.SERIES,
                status=MetadataMatchStatus.MATCHED,
                scoring_explanation=[],
                provider_external_ids=[],
            )
        )
        return source.id, target.id, target_season.id, episode.id

    source_id, target_id, target_season_id, episode_id = database.run_transaction(seed)
    merge_service = ManualItemMergeService(database)
    preview = merge_service.preview(source_item_id=source_id, target_item_id=target_id)
    backup = manual_item_merge_backup_path(database.database_path)
    database.backup_to(backup)

    merge_service.apply(
        source_item_id=source_id,
        target_item_id=target_id,
        preview_token=preview.preview_token,
        field_choices=tuple(
            ManualItemMergeFieldChoice(field=conflict.field, keep=ManualItemMergeSide.TARGET)
            for conflict in preview.conflicts
        ),
        backup_path=backup,
    )

    def merged_hierarchy(session: Session) -> tuple[Zaisan | None, int | None]:
        episode = session.get(Zaisan, episode_id)
        assert episode is not None
        return session.get(Zaisan, source_id), episode.parent_id

    source, episode_parent_id = database.run_transaction(merged_hierarchy)
    assert source is None
    assert episode_parent_id == target_season_id
    assert HierarchyRepairService(database).preview().actions == ()
    assert database.run_transaction(lambda session: session.get(Zaisan, target_id)) is not None


def test_series_path_aliases_ignore_unavailable_media(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    shows = tmp_path / "TVShows"
    directory_title = "LOTR; The Rings of Power"

    def aliases(session: Session) -> dict[str, Zaisan]:
        root = create_library_root(session, path=shows, expected_media_kind=ZaisanKind.SERIES)
        series = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.SERIES,
            title="The Lord of the Rings: The Rings of Power",
        )
        season = create_library_item(
            session,
            library_root_id=root.id,
            parent_id=series.id,
            item_kind=ZaisanKind.SEASON,
            title="Season 1",
            season_number=1,
        )
        episode = create_library_item(
            session,
            library_root_id=root.id,
            parent_id=season.id,
            item_kind=ZaisanKind.EPISODE,
            title="A Shadow of the Past",
            season_number=1,
            episode_number=1,
        )
        unavailable_file = attach_media_file(
            session,
            library_item_id=episode.id,
            absolute_path=shows / directory_title / "Season 01" / "S01E01.mkv",
            size_bytes=1,
            mtime_ns=1,
            container="matroska",
            availability=AvailabilityState.UNAVAILABLE,
        )
        return accepted_series_path_aliases(
            root_path=shows,
            layout=LibraryLayout.TV_SHOWS,
            items=(series, season, episode),
            media_files=(unavailable_file,),
            matched_series_ids={series.id},
        )

    assert database.run_transaction(aliases) == {}


def test_series_path_aliases_support_shows_within_a_generic_anime_root(tmp_path: Path) -> None:
    root_path = tmp_path / "Anime"
    directory_title = "Frieren"
    canonical_title = "Frieren: Beyond Journey's End"
    series = Zaisan(
        id=1,
        library_root_id=1,
        item_kind=ZaisanKind.SERIES,
        title=canonical_title,
        sort_title=canonical_title,
    )
    season = Zaisan(
        id=2,
        library_root_id=1,
        parent_id=series.id,
        item_kind=ZaisanKind.SEASON,
        title="Season 1",
        sort_title="Season 1",
        season_number=1,
    )
    episode = Zaisan(
        id=3,
        library_root_id=1,
        parent_id=season.id,
        item_kind=ZaisanKind.EPISODE,
        title="The Journey's End",
        sort_title="The Journey's End",
        season_number=1,
        episode_number=1,
    )
    media_file = MediaFile(
        library_item_id=episode.id,
        absolute_path=str(root_path / "Shows" / directory_title / "Season 01" / "S01E01.mkv"),
        availability=AvailabilityState.AVAILABLE,
    )

    aliases = accepted_series_path_aliases(
        root_path=root_path,
        layout=LibraryLayout.ANIME,
        items=(series, season, episode),
        media_files=(media_file,),
        matched_series_ids={series.id},
    )

    assert aliases == {series_title_identity(directory_title): series}


def test_series_path_aliases_reject_mixed_directory_ownership(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    shows = tmp_path / "TVShows"
    directory_title = "LOTR; The Rings of Power"

    def aliases(session: Session) -> dict[str, Zaisan]:
        root = create_library_root(session, path=shows, expected_media_kind=ZaisanKind.SERIES)
        canonical = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.SERIES,
            title="The Lord of the Rings: The Rings of Power",
        )
        canonical_season = create_library_item(
            session,
            library_root_id=root.id,
            parent_id=canonical.id,
            item_kind=ZaisanKind.SEASON,
            title="Season 1",
            season_number=1,
        )
        canonical_episode = create_library_item(
            session,
            library_root_id=root.id,
            parent_id=canonical_season.id,
            item_kind=ZaisanKind.EPISODE,
            title="A Shadow of the Past",
            season_number=1,
            episode_number=1,
        )
        conflicting = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.SERIES,
            title="Unmatched duplicate",
        )
        conflicting_season = create_library_item(
            session,
            library_root_id=root.id,
            parent_id=conflicting.id,
            item_kind=ZaisanKind.SEASON,
            title="Season 1",
            season_number=1,
        )
        conflicting_episode = create_library_item(
            session,
            library_root_id=root.id,
            parent_id=conflicting_season.id,
            item_kind=ZaisanKind.EPISODE,
            title="Adrift",
            season_number=1,
            episode_number=2,
        )
        canonical_file = attach_media_file(
            session,
            library_item_id=canonical_episode.id,
            absolute_path=shows / directory_title / "Season 01" / "S01E01.mkv",
            size_bytes=1,
            mtime_ns=1,
            container="matroska",
        )
        conflicting_file = attach_media_file(
            session,
            library_item_id=conflicting_episode.id,
            absolute_path=shows / directory_title / "Season 01" / "S01E02.mkv",
            size_bytes=1,
            mtime_ns=1,
            container="matroska",
        )
        session.add(
            MetadataBinding(
                library_item_id=canonical.id,
                provider="tmdb",
                provider_id="84773",
                provider_media_kind=ZaisanKind.SERIES,
                status=MetadataMatchStatus.MATCHED,
                scoring_explanation=[],
                provider_external_ids=[],
            )
        )
        return accepted_series_path_aliases(
            root_path=shows,
            layout=LibraryLayout.TV_SHOWS,
            items=(
                canonical,
                canonical_season,
                canonical_episode,
                conflicting,
                conflicting_season,
                conflicting_episode,
            ),
            media_files=(canonical_file, conflicting_file),
            matched_series_ids={canonical.id},
        )

    assert database.run_transaction(aliases) == {}


def test_series_title_identity_only_removes_the_article_token() -> None:
    assert series_title_identity("The Rings of Power") == "ringsofpower"
    assert series_title_identity("Theatre of Dreams") == "theatreofdreams"


def test_repair_backup_paths_are_unique(tmp_path: Path) -> None:
    database_path = tmp_path / "katalog.sqlite3"
    now = datetime(2026, 9, 6, 12, tzinfo=UTC)

    manual_first = manual_item_merge_backup_path(database_path, now)
    manual_second = manual_item_merge_backup_path(database_path, now)
    hierarchy_first = repair_backup_path(database_path, now)
    hierarchy_second = repair_backup_path(database_path, now)
    duplicate_first = duplicate_resolution_backup_path(database_path, now)
    duplicate_second = duplicate_resolution_backup_path(database_path, now)

    assert manual_first != manual_second
    assert hierarchy_first != hierarchy_second
    assert duplicate_first != duplicate_second
    assert manual_first.name.startswith("katalog.sqlite3.manual-item-merge-20260906T120000Z-")
    assert hierarchy_first.name.startswith("katalog.sqlite3.hierarchy-repair-20260906T120000Z-")
    assert duplicate_first.name.startswith("katalog.sqlite3.duplicate-resolution-20260906T120000Z-")
    assert manual_first.suffix == ".bak"


def test_manual_item_merge_rejects_a_changed_preview(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    movies = tmp_path / "Movies"

    def seed(session: Session) -> tuple[int, int]:
        root = create_library_root(session, path=movies, expected_media_kind=ZaisanKind.MOVIE)
        source = create_library_item(
            session, library_root_id=root.id, item_kind=ZaisanKind.MOVIE, title="Source title"
        )
        target = create_library_item(
            session, library_root_id=root.id, item_kind=ZaisanKind.MOVIE, title="Target title"
        )
        return source.id, target.id

    source_id, target_id = database.run_transaction(seed)
    service = ManualItemMergeService(database)
    preview = service.preview(source_item_id=source_id, target_item_id=target_id)

    def change_target_title(session: Session) -> None:
        target = session.get(Zaisan, target_id)
        assert target is not None
        target.title = "Changed target title"

    database.run_transaction(change_target_title)
    backup = manual_item_merge_backup_path(database.database_path)
    database.backup_to(backup)

    with pytest.raises(ManualItemMergeError, match="preview is stale"):
        service.apply(
            source_item_id=source_id,
            target_item_id=target_id,
            preview_token=preview.preview_token,
            field_choices=tuple(
                ManualItemMergeFieldChoice(field=conflict.field, keep=ManualItemMergeSide.TARGET)
                for conflict in preview.conflicts
            ),
            backup_path=backup,
        )

    def both_items_remain(session: Session) -> tuple[Zaisan | None, Zaisan | None]:
        return session.get(Zaisan, source_id), session.get(Zaisan, target_id)

    source, target = database.run_transaction(both_items_remain)
    assert source is not None
    assert target is not None


def test_manual_item_merge_rejects_a_movie_identity_created_by_field_choices(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    movies = tmp_path / "Movies"

    def seed(session: Session) -> tuple[int, int]:
        root = create_library_root(session, path=movies, expected_media_kind=ZaisanKind.MOVIE)
        source = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="Source film",
            release_year=2001,
        )
        target = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="Target film",
            release_year=2002,
        )
        create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="Source film",
            release_year=2002,
        )
        return source.id, target.id

    source_id, target_id = database.run_transaction(seed)
    service = ManualItemMergeService(database)
    preview = service.preview(source_item_id=source_id, target_item_id=target_id)
    backup = manual_item_merge_backup_path(database.database_path)
    database.backup_to(backup)

    with pytest.raises(ManualItemMergeError, match="sort title and release year"):
        service.apply(
            source_item_id=source_id,
            target_item_id=target_id,
            preview_token=preview.preview_token,
            field_choices=tuple(
                ManualItemMergeFieldChoice(
                    field=conflict.field,
                    keep=(
                        ManualItemMergeSide.SOURCE
                        if conflict.field is ManualItemMergeField.SORT_TITLE
                        else ManualItemMergeSide.TARGET
                    ),
                )
                for conflict in preview.conflicts
            ),
            backup_path=backup,
        )

    def source_and_target_remain(session: Session) -> tuple[Zaisan | None, Zaisan | None]:
        return session.get(Zaisan, source_id), session.get(Zaisan, target_id)

    source, target = database.run_transaction(source_and_target_remain)
    assert source is not None
    assert target is not None


def test_manual_item_merge_pairs_each_series_special_by_title(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    shows = tmp_path / "Shows"

    def seed(session: Session) -> tuple[int, int, tuple[int, ...]]:
        root = create_library_root(session, path=shows, expected_media_kind=ZaisanKind.SERIES)
        source = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.SERIES,
            title="Source show",
        )
        target = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.SERIES,
            title="Target show",
        )
        source_special_ids: list[int] = []
        for title in ("Behind the scenes", "Cast interview"):
            source_special = create_library_item(
                session,
                library_root_id=root.id,
                parent_id=source.id,
                item_kind=ZaisanKind.SPECIAL,
                title=title,
                season_number=0,
            )
            create_library_item(
                session,
                library_root_id=root.id,
                parent_id=target.id,
                item_kind=ZaisanKind.SPECIAL,
                title=title,
                season_number=0,
            )
            source_special_ids.append(source_special.id)
        return source.id, target.id, tuple(source_special_ids)

    source_id, target_id, source_special_ids = database.run_transaction(seed)
    service = ManualItemMergeService(database)
    preview = service.preview(source_item_id=source_id, target_item_id=target_id)

    assert preview.matched_descendant_count == 2
    backup = manual_item_merge_backup_path(database.database_path)
    database.backup_to(backup)
    service.apply(
        source_item_id=source_id,
        target_item_id=target_id,
        preview_token=preview.preview_token,
        field_choices=tuple(
            ManualItemMergeFieldChoice(field=conflict.field, keep=ManualItemMergeSide.TARGET)
            for conflict in preview.conflicts
        ),
        backup_path=backup,
    )

    def merged_specials(session: Session) -> tuple[tuple[Zaisan | None, ...], int]:
        retained = tuple(
            session.get(Zaisan, source_special_id) for source_special_id in source_special_ids
        )
        target_special_count = len(
            session.scalars(
                select(Zaisan).where(
                    Zaisan.parent_id == target_id,
                    Zaisan.item_kind == ZaisanKind.SPECIAL,
                )
            ).all()
        )
        return retained, target_special_count

    removed_specials, target_special_count = database.run_transaction(merged_specials)
    assert removed_specials == (None, None)
    assert target_special_count == 2


def test_duplicate_resolution_batch_merges_all_selected_candidates(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    movies = tmp_path / "Movies"

    def seed(session: Session) -> tuple[tuple[int, int], tuple[int, int]]:
        root = create_library_root(session, path=movies, expected_media_kind=ZaisanKind.MOVIE)
        pairs: list[tuple[int, int]] = []
        for title, provider_id in (("First Film", "101"), ("Second Film", "202")):
            source = create_library_item(
                session,
                library_root_id=root.id,
                item_kind=ZaisanKind.MOVIE,
                title=title,
            )
            target = create_library_item(
                session,
                library_root_id=root.id,
                item_kind=ZaisanKind.MOVIE,
                title=f"{title} (local)",
            )
            attach_media_file(
                session,
                library_item_id=target.id,
                absolute_path=movies / f"{provider_id}.mkv",
                size_bytes=1,
                mtime_ns=1,
                container="matroska",
            )
            session.add(
                MetadataBinding(
                    library_item_id=source.id,
                    provider="tmdb",
                    provider_id=provider_id,
                    provider_media_kind=ZaisanKind.MOVIE,
                    status=MetadataMatchStatus.MATCHED,
                    scoring_explanation=[],
                    provider_external_ids=[],
                )
            )
            session.add(
                MetadataCandidate(
                    library_item_id=target.id,
                    provider="tmdb",
                    provider_id=provider_id,
                    provider_media_kind=ZaisanKind.MOVIE,
                    provider_title=title,
                    confidence=1.0,
                    scoring_explanation=[],
                    status=MetadataCandidateStatus.SUGGESTED,
                    last_seen_at=datetime.now(UTC),
                )
            )
            pairs.append((source.id, target.id))
        first_pair, second_pair = pairs
        return first_pair, second_pair

    pairs = database.run_transaction(seed)
    service = DuplicateResolutionService(database)
    candidate_pairs = {
        (candidate.source_item_id, candidate.target_item_id) for candidate in service.preview()
    }
    assert candidate_pairs == set(pairs)
    backup = duplicate_resolution_backup_path(database.database_path)
    database.backup_to(backup)

    service.apply_many(resolutions=pairs, backup_path=backup)

    def resolved(session: Session) -> tuple[Zaisan | None, Zaisan | None, int]:
        return (
            session.get(Zaisan, pairs[0][0]),
            session.get(Zaisan, pairs[1][0]),
            len(session.scalars(select(Zaisan).where(Zaisan.item_kind == ZaisanKind.MOVIE)).all()),
        )

    assert database.run_transaction(resolved) == (None, None, 2)


def test_duplicate_resolution_merges_media_less_series_hierarchy_into_file_backed_series(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    series_root = tmp_path / "Series"

    def seed(session: Session) -> tuple[int, int, int, int]:
        root = create_library_root(session, path=series_root, expected_media_kind=ZaisanKind.SERIES)
        source = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.SERIES,
            title="Star Trek: Enterprise",
        )
        source_season = create_library_item(
            session,
            library_root_id=root.id,
            parent_id=source.id,
            item_kind=ZaisanKind.SEASON,
            title="Season 1",
            season_number=1,
        )
        target = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.SERIES,
            title="Star Trek; Enterprise",
        )
        target_season = create_library_item(
            session,
            library_root_id=root.id,
            parent_id=target.id,
            item_kind=ZaisanKind.SEASON,
            title="Season 1",
            season_number=1,
        )
        episode = create_library_item(
            session,
            library_root_id=root.id,
            parent_id=target_season.id,
            item_kind=ZaisanKind.EPISODE,
            title="Broken Bow",
            season_number=1,
            episode_number=1,
        )
        attach_media_file(
            session,
            library_item_id=episode.id,
            absolute_path=series_root / "Season 1" / "S01E01.mkv",
            size_bytes=1,
            mtime_ns=1,
            container="matroska",
        )
        session.add(
            MetadataBinding(
                library_item_id=source.id,
                provider="tmdb",
                provider_id="314",
                provider_media_kind=ZaisanKind.SERIES,
                status=MetadataMatchStatus.MATCHED,
                scoring_explanation=[],
                provider_external_ids=[],
            )
        )
        session.add(
            MetadataCandidate(
                library_item_id=target.id,
                provider="tmdb",
                provider_id="314",
                provider_media_kind=ZaisanKind.SERIES,
                provider_title="Star Trek: Enterprise",
                confidence=1.0,
                scoring_explanation=[],
                status=MetadataCandidateStatus.SUGGESTED,
                last_seen_at=datetime.now(UTC),
            )
        )
        collection = create_collection(session, name="Duplicate series collection")
        add_collection_membership(
            session,
            collection_id=collection.id,
            library_item_id=source_season.id,
        )
        return source.id, source_season.id, target.id, target_season.id

    source_id, source_season_id, target_id, target_season_id = database.run_transaction(seed)
    service = DuplicateResolutionService(database)

    candidates = service.preview()

    assert len(candidates) == 1
    assert (candidates[0].source_item_id, candidates[0].target_item_id) == (source_id, target_id)
    backup = duplicate_resolution_backup_path(database.database_path)
    database.backup_to(backup)
    service.apply(source_item_id=source_id, target_item_id=target_id, backup_path=backup)

    def resolved(
        session: Session,
    ) -> tuple[Zaisan | None, Zaisan, Zaisan | None, Zaisan, int, str, MetadataCandidateStatus]:
        target = session.get(Zaisan, target_id)
        target_season = session.get(Zaisan, target_season_id)
        assert target is not None
        assert target_season is not None
        binding = session.scalar(
            select(MetadataBinding).where(MetadataBinding.library_item_id == target.id)
        )
        candidate = session.scalar(
            select(MetadataCandidate).where(MetadataCandidate.library_item_id == target.id)
        )
        assert binding is not None
        assert candidate is not None
        return (
            session.get(Zaisan, source_id),
            target,
            session.get(Zaisan, source_season_id),
            target_season,
            len(target_season.collection_memberships),
            binding.provider_id,
            candidate.status,
        )

    (
        removed,
        target,
        removed_season,
        target_season,
        membership_count,
        provider_id,
        candidate_status,
    ) = database.run_transaction(resolved)
    assert backup.is_file()
    assert removed is None
    assert removed_season is None
    assert target.title == "Star Trek: Enterprise"
    assert provider_id == "314"
    assert candidate_status is MetadataCandidateStatus.ACCEPTED
    assert target_season.parent_id == target_id
    assert membership_count == 1


def test_duplicate_resolution_excludes_conflicting_provider_bindings(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    movies = tmp_path / "Movies"

    def seed(session: Session) -> None:
        root = create_library_root(session, path=movies, expected_media_kind=ZaisanKind.MOVIE)
        source = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="Canonical title",
        )
        target = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="File title",
        )
        attach_media_file(
            session,
            library_item_id=target.id,
            absolute_path=movies / "File title.mkv",
            size_bytes=1,
            mtime_ns=1,
            container="matroska",
        )
        session.add_all(
            (
                MetadataBinding(
                    library_item_id=source.id,
                    provider="tmdb",
                    provider_id="1",
                    provider_media_kind=ZaisanKind.MOVIE,
                    status=MetadataMatchStatus.MATCHED,
                    scoring_explanation=[],
                    provider_external_ids=[],
                ),
                MetadataBinding(
                    library_item_id=source.id,
                    provider="imdb",
                    provider_id="tt0000001",
                    provider_media_kind=ZaisanKind.MOVIE,
                    status=MetadataMatchStatus.MATCHED,
                    scoring_explanation=[],
                    provider_external_ids=[],
                ),
                MetadataBinding(
                    library_item_id=target.id,
                    provider="imdb",
                    provider_id="tt0000002",
                    provider_media_kind=ZaisanKind.MOVIE,
                    status=MetadataMatchStatus.MATCHED,
                    scoring_explanation=[],
                    provider_external_ids=[],
                ),
                MetadataCandidate(
                    library_item_id=target.id,
                    provider="tmdb",
                    provider_id="1",
                    provider_media_kind=ZaisanKind.MOVIE,
                    provider_title="Canonical title",
                    confidence=1.0,
                    scoring_explanation=[],
                    status=MetadataCandidateStatus.SUGGESTED,
                    last_seen_at=datetime.now(UTC),
                ),
            )
        )

    database.run_transaction(seed)

    assert DuplicateResolutionService(database).preview() == ()


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


def test_hierarchy_repair_does_not_reparent_already_correct_episodes_or_specials(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    shows = tmp_path / "TVShows"

    def seed(session: Session) -> None:
        root = create_library_root(session, path=shows, expected_media_kind=ZaisanKind.SERIES)
        series = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.SERIES,
            title="The Show Name",
        )
        season = create_library_item(
            session,
            library_root_id=root.id,
            parent_id=series.id,
            item_kind=ZaisanKind.SEASON,
            title="Season 1",
            season_number=1,
        )
        episode = create_library_item(
            session,
            library_root_id=root.id,
            parent_id=season.id,
            item_kind=ZaisanKind.EPISODE,
            title="Pilot",
            season_number=1,
            episode_number=1,
        )
        special = create_library_item(
            session,
            library_root_id=root.id,
            parent_id=series.id,
            item_kind=ZaisanKind.SPECIAL,
            title="Bonus",
            season_number=0,
        )
        attach_media_file(
            session,
            library_item_id=episode.id,
            absolute_path=shows / "Show Name" / "Season 01" / "S01E01.mkv",
            size_bytes=1,
            mtime_ns=1,
            container="matroska",
        )
        attach_media_file(
            session,
            library_item_id=special.id,
            absolute_path=shows / "Show Name" / "Season 00" / "Bonus.mkv",
            size_bytes=1,
            mtime_ns=1,
            container="matroska",
        )

    database.run_transaction(seed)

    assert HierarchyRepairService(database).preview().actions == ()


def test_hierarchy_repair_uses_a_matched_series_directory_alias(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    shows = tmp_path / "Shows"
    canonical_title = "The Lord of the Rings: The Rings of Power"
    directory_title = "LOTR; The Rings of Power"

    def seed(session: Session) -> tuple[int, int, int]:
        root = create_library_root(session, path=shows, expected_media_kind=ZaisanKind.SERIES)
        series = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.SERIES,
            title=canonical_title,
        )
        season = create_library_item(
            session,
            library_root_id=root.id,
            parent_id=series.id,
            item_kind=ZaisanKind.SEASON,
            title="Season 1",
            season_number=1,
        )
        established_episode = create_library_item(
            session,
            library_root_id=root.id,
            parent_id=season.id,
            item_kind=ZaisanKind.EPISODE,
            title="A Shadow of the Past",
            season_number=1,
            episode_number=1,
        )
        malformed = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="S01E02",
        )
        attach_media_file(
            session,
            library_item_id=established_episode.id,
            absolute_path=shows / directory_title / "Season 01" / "S01E01.mkv",
            size_bytes=1,
            mtime_ns=1,
            container="matroska",
        )
        attach_media_file(
            session,
            library_item_id=malformed.id,
            absolute_path=shows / directory_title / "Season 01" / "S01E02.mkv",
            size_bytes=1,
            mtime_ns=1,
            container="matroska",
        )
        session.add(
            MetadataBinding(
                library_item_id=series.id,
                provider="tmdb",
                provider_id="84773",
                provider_media_kind=ZaisanKind.SERIES,
                status=MetadataMatchStatus.MATCHED,
                scoring_explanation=[],
                provider_external_ids=[],
            )
        )
        return series.id, season.id, malformed.id

    series_id, season_id, malformed_id = database.run_transaction(seed)
    service = HierarchyRepairService(database)

    preview = service.preview()

    assert {action.kind for action in preview.actions} == {
        RepairActionKind.RETYPE,
        RepairActionKind.REPARENT,
    }
    reparent = next(
        action for action in preview.actions if action.kind is RepairActionKind.REPARENT
    )
    assert reparent.target_series_title == canonical_title
    backup = repair_backup_path(database.database_path)
    database.backup_to(backup)
    service.apply(backup_path=backup)

    def repaired(session: Session) -> tuple[int | None, ZaisanKind, int]:
        episode = session.get(Zaisan, malformed_id)
        assert episode is not None
        return episode.parent_id, episode.item_kind, len(
            session.scalars(
                select(Zaisan).where(
                    Zaisan.library_root_id == episode.library_root_id,
                    Zaisan.item_kind == ZaisanKind.SERIES,
                )
            ).all()
        )

    parent_id, item_kind, series_count = database.run_transaction(repaired)
    assert parent_id == season_id
    assert item_kind is ZaisanKind.EPISODE
    assert series_count == 1
    assert database.run_transaction(lambda session: session.get(Zaisan, series_id)) is not None


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


def test_hierarchy_repair_reidentifies_a_container_movie_as_the_correct_remake(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    movies = tmp_path / "Movies"

    def seed(session: Session) -> tuple[int, int]:
        root = create_library_root(session, path=movies, expected_media_kind=ZaisanKind.MOVIE)
        original = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="Ghostbusters",
            release_year=1984,
        )
        container = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="00's",
        )
        attach_media_file(
            session,
            library_item_id=container.id,
            absolute_path=movies / "00's" / "Ghostbusters (2016).mkv",
            size_bytes=1,
            mtime_ns=1,
            container="matroska",
        )
        return original.id, container.id

    original_id, container_id = database.run_transaction(seed)
    service = HierarchyRepairService(database)

    preview = service.preview()

    rename = next(action for action in preview.actions if action.item_id == container_id)
    assert rename.kind is RepairActionKind.RENAME
    assert rename.target_title == "Ghostbusters"
    assert rename.target_release_year == 2016
    backup = repair_backup_path(database.database_path)
    database.backup_to(backup)
    service.apply(backup_path=backup)

    def repaired_identities(
        session: Session,
    ) -> tuple[tuple[str, int | None], tuple[str, int | None]]:
        original = session.get(Zaisan, original_id)
        container = session.get(Zaisan, container_id)
        assert original is not None
        assert container is not None
        return (original.title, original.release_year), (container.title, container.release_year)

    assert database.run_transaction(repaired_identities) == (
        ("Ghostbusters", 1984),
        ("Ghostbusters", 2016),
    )


def test_hierarchy_repair_merges_a_yearless_path_with_its_known_remake(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    movies = tmp_path / "Movies"

    def seed(session: Session) -> tuple[int, int]:
        root = create_library_root(session, path=movies, expected_media_kind=ZaisanKind.MOVIE)
        remake = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="Ghostbusters",
            release_year=2016,
        )
        container = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="00's",
            release_year=2016,
        )
        attach_media_file(
            session,
            library_item_id=container.id,
            absolute_path=movies / "00's" / "Ghostbusters.mkv",
            size_bytes=1,
            mtime_ns=1,
            container="matroska",
        )
        return remake.id, container.id

    remake_id, container_id = database.run_transaction(seed)
    service = HierarchyRepairService(database)

    preview = service.preview()

    merge = next(action for action in preview.actions if action.kind is RepairActionKind.MERGE)
    assert (merge.item_id, merge.target_item_id) == (container_id, remake_id)
    backup = repair_backup_path(database.database_path)
    database.backup_to(backup)
    service.apply(backup_path=backup)

    def repaired_media_owner(session: Session) -> tuple[bool, int]:
        media_owner_id = session.scalar(select(MediaFile.library_item_id))
        assert media_owner_id is not None
        return session.get(Zaisan, container_id) is None, media_owner_id

    assert database.run_transaction(repaired_media_owner) == (True, remake_id)


def test_hierarchy_repair_retypes_an_episode_as_the_correct_movie_remake(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    movies = tmp_path / "Movies"

    def seed(session: Session) -> tuple[int, int]:
        root = create_library_root(session, path=movies, expected_media_kind=ZaisanKind.MOVIE)
        original = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="Ghostbusters",
            release_year=1984,
        )
        misclassified = Zaisan(
            library_root_id=root.id,
            item_kind=ZaisanKind.EPISODE,
            title="Wrong episode",
            sort_title="Wrong episode",
            season_number=1,
            episode_number=1,
        )
        session.add(misclassified)
        session.flush()
        attach_media_file(
            session,
            library_item_id=misclassified.id,
            absolute_path=movies / "Ghostbusters (2016).mkv",
            size_bytes=1,
            mtime_ns=1,
            container="matroska",
        )
        return original.id, misclassified.id

    original_id, misclassified_id = database.run_transaction(seed)
    service = HierarchyRepairService(database)

    preview = service.preview()

    actions = tuple(action for action in preview.actions if action.item_id == misclassified_id)
    assert [action.kind for action in actions] == [RepairActionKind.RETYPE, RepairActionKind.RENAME]
    assert actions[-1].target_title == "Ghostbusters"
    assert actions[-1].target_release_year == 2016
    backup = repair_backup_path(database.database_path)
    database.backup_to(backup)
    service.apply(backup_path=backup)

    def repaired_identities(
        session: Session,
    ) -> tuple[tuple[ZaisanKind, str, int | None], tuple[ZaisanKind, str, int | None]]:
        original = session.get(Zaisan, original_id)
        misclassified = session.get(Zaisan, misclassified_id)
        assert original is not None
        assert misclassified is not None
        return (
            (original.item_kind, original.title, original.release_year),
            (misclassified.item_kind, misclassified.title, misclassified.release_year),
        )

    assert database.run_transaction(repaired_identities) == (
        (ZaisanKind.MOVIE, "Ghostbusters", 1984),
        (ZaisanKind.MOVIE, "Ghostbusters", 2016),
    )


def test_hierarchy_repair_reparents_an_extra_to_the_correct_movie_remake(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    movies = tmp_path / "Movies"

    def seed(session: Session) -> tuple[int, int]:
        root = create_library_root(session, path=movies, expected_media_kind=ZaisanKind.MOVIE)
        create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="Ghostbusters",
            release_year=1984,
        )
        remake = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="Ghostbusters",
            release_year=2016,
        )
        orphan = Zaisan(
            library_root_id=root.id,
            item_kind=ZaisanKind.EXTRA,
            title="Trailer",
            sort_title="Trailer",
        )
        session.add(orphan)
        session.flush()
        attach_media_file(
            session,
            library_item_id=orphan.id,
            absolute_path=movies / "Ghostbusters (2016)" / "Extras" / "Trailer.mkv",
            size_bytes=1,
            mtime_ns=1,
            container="matroska",
        )
        return orphan.id, remake.id

    orphan_id, remake_id = database.run_transaction(seed)
    service = HierarchyRepairService(database)

    preview = service.preview()

    reparent = next(action for action in preview.actions if action.item_id == orphan_id)
    assert reparent.kind is RepairActionKind.REPARENT
    assert reparent.target_release_year == 2016
    backup = repair_backup_path(database.database_path)
    database.backup_to(backup)
    service.apply(backup_path=backup)

    def repaired_parent_id(session: Session) -> int | None:
        orphan = session.get(Zaisan, orphan_id)
        assert orphan is not None
        return orphan.parent_id

    assert database.run_transaction(repaired_parent_id) == remake_id

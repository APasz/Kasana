from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from kasana.katalog.api.contracts import (
    CollectionCreate,
    CollectionDetailsUpdate,
    CollectionMembershipAddition,
    CollectionMembershipBatchRequest,
    CollectionMembershipCreate,
    CollectionMembershipLookupRequest,
    CollectionUpdate,
    WatchOrderCreate,
    WatchOrderEntriesCreate,
    WatchOrderEntryCreate,
    WatchOrderEntryMove,
    WatchOrderGenerationApplyMode,
    WatchOrderGenerationMode,
    WatchOrderGenerationRequest,
    WatchOrderKind,
    WatchOrderUpdate,
)
from kasana.katalog.api.service import (
    CatalogueConflictError,
    CatalogueNotFoundError,
    CatalogueValidationError,
    KatalogQueryService,
    _watch_order_entry_is_unavailable,  # pyright: ignore[reportPrivateUsage]
)
from kasana.katalog.database import KatalogDatabase
from kasana.katalog.models import (
    AvailabilityState,
    CachedArtwork,
    CachedArtworkKind,
    KeiroEntry,
    Zaisan,
    ZaisanKind,
)
from kasana.katalog.services import attach_media_file, create_library_item, create_library_root


def _queries(database: KatalogDatabase, tmp_path: Path) -> KatalogQueryService:
    return KatalogQueryService(database, artwork_cache_path=tmp_path / "artwork")


def _library(database: KatalogDatabase, tmp_path: Path) -> dict[str, int]:
    def create(session: Session) -> dict[str, int]:
        root = create_library_root(
            session,
            path=tmp_path / "library",
            expected_media_kind=ZaisanKind.MOVIE,
        )
        movie = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="Movie",
            release_date=date(2000, 1, 1),
        )
        series = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.SERIES,
            title="Series",
        )
        season = create_library_item(
            session,
            library_root_id=root.id,
            parent_id=series.id,
            item_kind=ZaisanKind.SEASON,
            title="Season 1",
            season_number=1,
        )
        first_episode = create_library_item(
            session,
            library_root_id=root.id,
            parent_id=season.id,
            item_kind=ZaisanKind.EPISODE,
            title="Episode one",
            season_number=1,
            episode_number=1,
            release_date=date(2020, 1, 1),
            air_date=date(2010, 1, 1),
        )
        second_episode = create_library_item(
            session,
            library_root_id=root.id,
            parent_id=season.id,
            item_kind=ZaisanKind.EPISODE,
            title="Episode two",
            season_number=1,
            episode_number=2,
            release_date=date(2019, 1, 1),
            air_date=date(2012, 1, 1),
        )
        unavailable_extra = create_library_item(
            session,
            library_root_id=root.id,
            parent_id=movie.id,
            item_kind=ZaisanKind.EXTRA,
            title="Unavailable extra",
            availability=AvailabilityState.UNAVAILABLE,
        )
        empty_season = create_library_item(
            session,
            library_root_id=root.id,
            parent_id=series.id,
            item_kind=ZaisanKind.SEASON,
            title="Season 2",
            season_number=2,
        )
        for item, filename in (
            (movie, "movie.mkv"),
            (first_episode, "episode-one.mkv"),
            (second_episode, "episode-two.mkv"),
        ):
            attach_media_file(
                session,
                library_item_id=item.id,
                absolute_path=tmp_path / filename,
                size_bytes=1,
                mtime_ns=0,
                container="matroska",
            )
        return {
            "movie": movie.id,
            "series": series.id,
            "first_episode": first_episode.id,
            "second_episode": second_episode.id,
            "unavailable_extra": unavailable_extra.id,
            "empty_season": empty_season.id,
        }

    return database.run_transaction(create)


def test_collection_membership_revisions_and_deletion_safety(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    library = _library(database, tmp_path)
    queries = _queries(database, tmp_path)

    collection = queries.create_collection(CollectionCreate(name="Stargate"))
    first = queries.add_collection_membership(
        collection.collection_id,
        CollectionMembershipCreate(
            expected_revision=collection.revision,
            library_item_id=library["movie"],
        ),
    )
    second = queries.add_collection_membership(
        collection.collection_id,
        CollectionMembershipCreate(
            expected_revision=first.revision,
            library_item_id=library["series"],
        ),
    )
    detail = queries.get_collection(collection.collection_id)

    assert detail.revision == second.revision
    assert [member.item.id for member in detail.members] == [library["movie"], library["series"]]
    assert "library" not in detail.model_dump_json()
    with pytest.raises(CatalogueValidationError, match="already"):
        queries.add_collection_membership(
            collection.collection_id,
            CollectionMembershipCreate(
                expected_revision=second.revision,
                library_item_id=library["movie"],
            ),
        )
    with pytest.raises(CatalogueConflictError, match="expected revision"):
        queries.add_collection_membership(
            collection.collection_id,
            CollectionMembershipCreate(
                expected_revision=first.revision,
                library_item_id=library["unavailable_extra"],
            ),
        )

    deleted = queries.delete_collection(collection.collection_id, expected_revision=second.revision)
    assert deleted.deleted is True
    assert (
        database.run_transaction(lambda session: session.get(Zaisan, library["movie"])) is not None
    )


def test_collection_membership_batch_is_atomic_and_bumps_revision_once(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    library = _library(database, tmp_path)
    queries = _queries(database, tmp_path)
    collection = queries.create_collection(CollectionCreate(name="Mixed"))
    movie = queries.add_collection_membership(
        collection.collection_id,
        CollectionMembershipCreate(
            expected_revision=collection.revision,
            library_item_id=library["movie"],
        ),
    )
    series = queries.add_collection_membership(
        collection.collection_id,
        CollectionMembershipCreate(
            expected_revision=movie.revision,
            library_item_id=library["series"],
        ),
    )

    result = queries.batch_collection_memberships(
        collection.collection_id,
        CollectionMembershipBatchRequest(
            expected_revision=series.revision,
            additions=(CollectionMembershipAddition(library_item_id=library["first_episode"]),),
            removals=(library["series"],),
        ),
    )

    assert result.membership is None
    assert result.revision == series.revision + 1
    detail = queries.get_collection(collection.collection_id)
    assert detail.revision == result.revision
    assert [member.item.id for member in detail.members] == [
        library["movie"],
        library["first_episode"],
    ]

    with pytest.raises(CatalogueConflictError, match="expected revision"):
        queries.batch_collection_memberships(
            collection.collection_id,
            CollectionMembershipBatchRequest(
                expected_revision=series.revision,
                additions=(
                    CollectionMembershipAddition(library_item_id=library["second_episode"]),
                ),
            ),
        )
    with pytest.raises(CatalogueValidationError, match="already"):
        queries.batch_collection_memberships(
            collection.collection_id,
            CollectionMembershipBatchRequest(
                expected_revision=result.revision,
                additions=(CollectionMembershipAddition(library_item_id=library["movie"]),),
            ),
        )
    with pytest.raises(CatalogueNotFoundError, match="does not exist"):
        queries.batch_collection_memberships(
            collection.collection_id,
            CollectionMembershipBatchRequest(
                expected_revision=result.revision,
                additions=(CollectionMembershipAddition(library_item_id=999_999),),
            ),
        )

    unchanged = queries.get_collection(collection.collection_id)
    assert unchanged.revision == result.revision
    assert unchanged.members[0].item.id == library["movie"]

    with pytest.raises(ValueError, match="must not repeat"):
        CollectionMembershipBatchRequest(
            expected_revision=result.revision,
            additions=(
                CollectionMembershipAddition(library_item_id=library["movie"]),
                CollectionMembershipAddition(library_item_id=library["movie"]),
            ),
        )


def test_watch_order_entry_moves_and_generation_preview(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    library = _library(database, tmp_path)
    queries = _queries(database, tmp_path)
    collection = queries.create_collection(CollectionCreate(name="Mixed"))
    revision = collection.revision
    for key in ("movie", "series", "first_episode", "unavailable_extra", "empty_season"):
        membership = queries.add_collection_membership(
            collection.collection_id,
            CollectionMembershipCreate(expected_revision=revision, library_item_id=library[key]),
        )
        revision = membership.revision
    order = queries.create_watch_order(
        collection.collection_id,
        WatchOrderCreate(
            expected_collection_revision=revision,
            name="Release",
            kind=WatchOrderKind.CUSTOM,
        ),
    )
    first = queries.add_watch_order_entry(
        order.watch_order_id,
        WatchOrderEntryCreate(expected_revision=order.revision, library_item_id=library["movie"]),
    )
    second = queries.add_watch_order_entry(
        order.watch_order_id,
        WatchOrderEntryCreate(
            expected_revision=first.revision, library_item_id=library["first_episode"]
        ),
    )
    assert first.entry is not None
    assert second.entry is not None
    moved = queries.move_watch_order_entry(
        order.watch_order_id,
        second.entry.id,
        WatchOrderEntryMove(expected_revision=second.revision, move_before_entry_id=first.entry.id),
    )
    entries = queries.get_watch_order(order.watch_order_id, cursor=None, limit=10).entries.items
    assert [(entry.position, entry.item.id) for entry in entries] == [
        (0, library["first_episode"]),
        (1, library["movie"]),
    ]

    preview = queries.preview_watch_order_generation(
        order.watch_order_id,
        WatchOrderGenerationRequest(
            expected_revision=moved.revision,
            mode=WatchOrderGenerationMode.RELEASE,
        ),
    )
    assert [item.id for item in preview.entries] == [
        library["movie"],
        library["second_episode"],
        library["first_episode"],
        library["unavailable_extra"],
    ]
    assert [item.id for item in preview.undated_items] == [library["unavailable_extra"]]
    assert [item.id for item in preview.unavailable_items] == [library["unavailable_extra"]]
    assert [item.id for item in preview.duplicate_items] == [library["first_episode"]]
    assert [item.id for item in preview.non_playable_items] == [library["empty_season"]]

    air_preview = queries.preview_watch_order_generation(
        order.watch_order_id,
        WatchOrderGenerationRequest(
            expected_revision=moved.revision,
            mode=WatchOrderGenerationMode.AIR,
        ),
    )
    assert [item.id for item in air_preview.entries] == [
        library["movie"],
        library["first_episode"],
        library["second_episode"],
        library["unavailable_extra"],
    ]
    applied = queries.apply_watch_order_generation(
        order.watch_order_id,
        WatchOrderGenerationRequest(
            expected_revision=moved.revision,
            mode=WatchOrderGenerationMode.AIR,
            apply_mode=WatchOrderGenerationApplyMode.REPLACE,
        ),
    )
    assert applied.revision == moved.revision + 1
    persisted = database.run_transaction(
        lambda session: tuple(
            session.scalars(
                select(KeiroEntry)
                .where(KeiroEntry.watch_order_id == order.watch_order_id)
                .order_by(KeiroEntry.position)
            )
        )
    )
    assert [entry.library_item_id for entry in persisted] == [
        library["movie"],
        library["first_episode"],
        library["second_episode"],
        library["unavailable_extra"],
    ]


def test_deleting_an_item_compacts_watch_order_positions_before_a_merge(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    library = _library(database, tmp_path)
    queries = _queries(database, tmp_path)
    collection = queries.create_collection(CollectionCreate(name="Mixed"))
    revision = collection.revision
    for key in ("movie", "first_episode", "second_episode"):
        membership = queries.add_collection_membership(
            collection.collection_id,
            CollectionMembershipCreate(expected_revision=revision, library_item_id=library[key]),
        )
        revision = membership.revision
    order = queries.create_watch_order(
        collection.collection_id,
        WatchOrderCreate(
            expected_collection_revision=revision,
            name="Release",
            kind=WatchOrderKind.CUSTOM,
        ),
    )
    first = queries.add_watch_order_entry(
        order.watch_order_id,
        WatchOrderEntryCreate(expected_revision=order.revision, library_item_id=library["movie"]),
    )
    second = queries.add_watch_order_entry(
        order.watch_order_id,
        WatchOrderEntryCreate(
            expected_revision=first.revision,
            library_item_id=library["first_episode"],
        ),
    )

    queries.delete_item(library["movie"], confirm=True)

    after_deletion = queries.get_watch_order(order.watch_order_id, cursor=None, limit=10)
    assert [(entry.position, entry.item.id) for entry in after_deletion.entries.items] == [
        (0, library["first_episode"])
    ]
    applied = queries.apply_watch_order_generation(
        order.watch_order_id,
        WatchOrderGenerationRequest(
            expected_revision=after_deletion.watch_order.revision,
            mode=WatchOrderGenerationMode.RELEASE,
            apply_mode=WatchOrderGenerationApplyMode.MERGE,
        ),
    )
    merged = queries.get_watch_order(order.watch_order_id, cursor=None, limit=10)

    assert applied.revision == second.revision + 2
    assert [(entry.position, entry.item.id) for entry in merged.entries.items] == [
        (0, library["first_episode"]),
        (1, library["second_episode"]),
    ]


def test_watch_order_batch_entry_insertion_is_contiguous_and_atomic(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    library = _library(database, tmp_path)
    queries = _queries(database, tmp_path)
    collection = queries.create_collection(CollectionCreate(name="Mixed"))
    order = queries.create_watch_order(
        collection.collection_id,
        WatchOrderCreate(
            expected_collection_revision=collection.revision,
            name="Chronological",
            kind=WatchOrderKind.CHRONOLOGICAL,
        ),
    )
    movie = queries.add_watch_order_entry(
        order.watch_order_id,
        WatchOrderEntryCreate(expected_revision=order.revision, library_item_id=library["movie"]),
    )
    assert movie.entry is not None
    batch = queries.add_watch_order_entries(
        order.watch_order_id,
        WatchOrderEntriesCreate(
            expected_revision=movie.revision,
            library_item_ids=(library["first_episode"], library["second_episode"]),
            insert_before_entry_id=movie.entry.id,
        ),
    )
    entries = queries.get_watch_order(order.watch_order_id, cursor=None, limit=10).entries.items
    assert batch.revision == movie.revision + 1
    assert [entry.item.id for entry in entries] == [
        library["first_episode"],
        library["second_episode"],
        library["movie"],
    ]
    with pytest.raises(CatalogueValidationError, match="already in this watch order"):
        queries.add_watch_order_entries(
            order.watch_order_id,
            WatchOrderEntriesCreate(
                expected_revision=batch.revision,
                library_item_ids=(library["second_episode"],),
            ),
        )


def test_watch_order_batch_contract_rejects_duplicate_items_and_dual_anchors() -> None:
    with pytest.raises(ValueError, match="cannot contain duplicate"):
        WatchOrderEntriesCreate(expected_revision=1, library_item_ids=(1, 1))
    with pytest.raises(ValueError, match="before and after"):
        WatchOrderEntriesCreate(
            expected_revision=1,
            library_item_ids=(1,),
            insert_before_entry_id=2,
            insert_after_entry_id=3,
        )


def test_collection_preferences_select_artwork_and_default_order(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    library = _library(database, tmp_path)
    queries = _queries(database, tmp_path)
    collection = queries.create_collection(CollectionCreate(name="Stargate"))
    membership = queries.add_collection_membership(
        collection.collection_id,
        CollectionMembershipCreate(
            expected_revision=collection.revision,
            library_item_id=library["movie"],
        ),
    )
    release = queries.create_watch_order(
        collection.collection_id,
        WatchOrderCreate(
            expected_collection_revision=membership.revision,
            name="Release",
            kind=WatchOrderKind.CUSTOM,
        ),
    )
    alternative = queries.create_watch_order(
        collection.collection_id,
        WatchOrderCreate(
            expected_collection_revision=release.collection_revision,
            name="Chronological",
            kind=WatchOrderKind.CHRONOLOGICAL,
        ),
    )

    def add_poster(session: Session) -> int:
        movie = session.get(Zaisan, library["movie"])
        assert movie is not None
        poster = CachedArtwork(
            library_item_id=movie.id,
            provider="fixture",
            provider_id="movie",
            artwork_kind=CachedArtworkKind.POSTER,
            provider_revision="1",
            source_url="https://example.test/movie.jpg",
            attribution=None,
            content_type="image/jpeg",
            cache_relative_path="movie.jpg",
            size_bytes=1,
            downloaded_at=datetime.now(UTC),
        )
        session.add(poster)
        session.flush()
        movie.selected_artwork_ids = {"poster": poster.id}
        return poster.id

    poster_id = database.run_transaction(add_poster)
    updated = queries.update_collection(
        collection.collection_id,
        CollectionUpdate(
            expected_revision=alternative.collection_revision,
            artwork_item_id=library["movie"],
            default_watch_order_id=alternative.watch_order_id,
        ),
    )
    detail = queries.get_collection(collection.collection_id)
    movie_detail = queries.get_item(library["movie"])

    assert updated.revision == alternative.collection_revision + 1
    assert detail.artwork_item_id == library["movie"]
    assert detail.default_watch_order_id == alternative.watch_order_id
    assert detail.representative_artwork is not None
    assert detail.representative_artwork.id == poster_id
    assert [(order.id, order.is_default) for order in detail.watch_orders] == [
        (alternative.watch_order_id, True),
        (release.watch_order_id, False),
    ]
    assert [entry.id for entry in movie_detail.collections] == [collection.collection_id]


def test_collection_preferences_reject_invalid_choices_and_reassign_a_deleted_default(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    library = _library(database, tmp_path)
    queries = _queries(database, tmp_path)
    collection = queries.create_collection(CollectionCreate(name="Stargate"))
    movie_membership = queries.add_collection_membership(
        collection.collection_id,
        CollectionMembershipCreate(
            expected_revision=collection.revision,
            library_item_id=library["movie"],
        ),
    )
    series_membership = queries.add_collection_membership(
        collection.collection_id,
        CollectionMembershipCreate(
            expected_revision=movie_membership.revision,
            library_item_id=library["series"],
        ),
    )
    release = queries.create_watch_order(
        collection.collection_id,
        WatchOrderCreate(
            expected_collection_revision=series_membership.revision,
            name="Release",
            kind=WatchOrderKind.CUSTOM,
        ),
    )
    chronological = queries.create_watch_order(
        collection.collection_id,
        WatchOrderCreate(
            expected_collection_revision=release.collection_revision,
            name="Chronological",
            kind=WatchOrderKind.CHRONOLOGICAL,
        ),
    )
    other_collection = queries.create_collection(CollectionCreate(name="Atlantis"))
    other_order = queries.create_watch_order(
        other_collection.collection_id,
        WatchOrderCreate(
            expected_collection_revision=other_collection.revision,
            name="Release",
            kind=WatchOrderKind.CUSTOM,
        ),
    )

    with pytest.raises(CatalogueValidationError, match="direct collection member"):
        queries.update_collection(
            collection.collection_id,
            CollectionUpdate(
                expected_revision=chronological.collection_revision,
                artwork_item_id=library["first_episode"],
            ),
        )
    with pytest.raises(CatalogueValidationError, match="cached poster"):
        queries.update_collection(
            collection.collection_id,
            CollectionUpdate(
                expected_revision=chronological.collection_revision,
                artwork_item_id=library["series"],
            ),
        )
    with pytest.raises(CatalogueValidationError, match="requires a default"):
        queries.update_collection(
            collection.collection_id,
            CollectionUpdate(
                expected_revision=chronological.collection_revision,
                default_watch_order_id=None,
            ),
        )
    with pytest.raises(CatalogueValidationError, match="must belong"):
        queries.update_collection(
            collection.collection_id,
            CollectionUpdate(
                expected_revision=chronological.collection_revision,
                default_watch_order_id=other_order.watch_order_id,
            ),
        )

    queries.delete_watch_order(release.watch_order_id, expected_revision=release.revision)
    detail = queries.get_collection(collection.collection_id)

    assert detail.default_watch_order_id == chronological.watch_order_id


def test_watch_order_unavailability_distinguishes_non_playable_members(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    library = _library(database, tmp_path)

    def availability(session: Session) -> tuple[bool, bool, bool]:
        movie = session.get(Zaisan, library["movie"])
        series = session.get(Zaisan, library["series"])
        unavailable_extra = session.get(Zaisan, library["unavailable_extra"])
        assert movie is not None
        assert series is not None
        assert unavailable_extra is not None
        return (
            _watch_order_entry_is_unavailable(movie, ()),
            _watch_order_entry_is_unavailable(series, ()),
            _watch_order_entry_is_unavailable(unavailable_extra, ()),
        )

    assert database.run_transaction(availability) == (True, False, True)


def test_collection_details_and_titles_save_together(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    library = _library(database, tmp_path)
    queries = _queries(database, tmp_path)
    collection = queries.create_collection(CollectionCreate(name="Original"))
    result = queries.batch_collection_memberships(
        collection.collection_id,
        CollectionMembershipBatchRequest(
            expected_revision=1,
            details=CollectionDetailsUpdate(name="Stargate", overview="A complete order"),
            additions=(CollectionMembershipAddition(library_item_id=library["series"]),),
        ),
    )
    detail = queries.get_collection(collection.collection_id)
    assert (result.revision, detail.name, detail.overview) == (2, "Stargate", "A complete order")
    assert [member.item.id for member in detail.members] == [library["series"]]
    with pytest.raises(CatalogueValidationError):
        queries.batch_collection_memberships(
            collection.collection_id,
            CollectionMembershipBatchRequest(
                expected_revision=2,
                details=CollectionDetailsUpdate(name="Invalid", artwork_item_id=library["movie"]),
                additions=(CollectionMembershipAddition(library_item_id=library["movie"]),),
            ),
        )
    unchanged = queries.get_collection(collection.collection_id)
    assert unchanged == detail


def test_full_order_save_preserves_entry_ids_and_rolls_back_invalid_edits(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    library = _library(database, tmp_path)
    queries = _queries(database, tmp_path)
    collection = queries.create_collection(CollectionCreate(name="Franchise"))
    order = queries.create_watch_order(
        collection.collection_id,
        WatchOrderCreate(expected_collection_revision=1, name="Draft", kind=WatchOrderKind.CUSTOM),
    )
    ids = (library["first_episode"], library["movie"], library["second_episode"])
    queries.update_watch_order(
        order.watch_order_id, WatchOrderUpdate(expected_revision=1, item_ids=ids)
    )
    first = queries.get_watch_order(order.watch_order_id, cursor=None, limit=10)
    result = queries.update_watch_order(
        order.watch_order_id,
        WatchOrderUpdate(
            expected_revision=2,
            name="Recommended",
            kind=WatchOrderKind.RECOMMENDED,
            item_ids=tuple(reversed(ids)),
        ),
    )
    saved = queries.get_watch_order(order.watch_order_id, cursor=None, limit=10)
    assert result.revision == 3
    assert saved.watch_order.name == "Recommended"
    assert [entry.item.id for entry in saved.entries.items] == list(reversed(ids))
    assert [entry.id for entry in saved.entries.items] == [
        entry.id for entry in reversed(first.entries.items)
    ]
    with pytest.raises(CatalogueValidationError, match="playable"):
        queries.update_watch_order(
            order.watch_order_id,
            WatchOrderUpdate(expected_revision=3, name="Bad draft", item_ids=(library["series"],)),
        )
    assert queries.get_watch_order(order.watch_order_id, cursor=None, limit=10) == saved
    queries.update_watch_order(
        order.watch_order_id, WatchOrderUpdate(expected_revision=3, item_ids=())
    )
    assert queries.get_watch_order(order.watch_order_id, cursor=None, limit=10).entries.items == ()


def test_generation_preview_matches_merge_and_rejects_changed_collection(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    library = _library(database, tmp_path)
    queries = _queries(database, tmp_path)
    collection = queries.create_collection(CollectionCreate(name="Franchise"))
    members = queries.batch_collection_memberships(
        collection.collection_id,
        CollectionMembershipBatchRequest(
            expected_revision=1,
            additions=(CollectionMembershipAddition(library_item_id=library["series"]),),
        ),
    )
    order = queries.create_watch_order(
        collection.collection_id,
        WatchOrderCreate(
            expected_collection_revision=members.revision,
            name="Chronological",
            kind=WatchOrderKind.CHRONOLOGICAL,
        ),
    )
    queries.update_watch_order(
        order.watch_order_id,
        WatchOrderUpdate(expected_revision=1, item_ids=(library["second_episode"],)),
    )
    request = WatchOrderGenerationRequest(
        expected_revision=2,
        mode=WatchOrderGenerationMode.AIR,
        apply_mode=WatchOrderGenerationApplyMode.MERGE,
    )
    preview = queries.preview_watch_order_generation(order.watch_order_id, request)
    assert [item.id for item in preview.entries] == [
        library["second_episode"],
        library["first_episode"],
    ]
    applied = queries.apply_watch_order_generation(
        order.watch_order_id, request.model_copy(update={"preview_token": preview.preview_token})
    )
    saved = queries.get_watch_order(order.watch_order_id, cursor=None, limit=10)
    assert [entry.item.id for entry in saved.entries.items] == [item.id for item in preview.entries]
    request = request.model_copy(update={"expected_revision": applied.revision})
    preview = queries.preview_watch_order_generation(order.watch_order_id, request)
    queries.add_collection_membership(
        collection.collection_id,
        CollectionMembershipCreate(
            expected_revision=order.collection_revision, library_item_id=library["movie"]
        ),
    )
    with pytest.raises(CatalogueConflictError, match="preview changed"):
        queries.apply_watch_order_generation(
            order.watch_order_id,
            request.model_copy(update={"preview_token": preview.preview_token}),
        )
    assert queries.get_watch_order(order.watch_order_id, cursor=None, limit=10) == saved


def test_generated_orders_use_episode_dates_and_natural_ties_and_can_be_copied(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    library = _library(database, tmp_path)
    queries = _queries(database, tmp_path)
    with database.transaction() as session:
        for key, title in (("first_episode", "Z title"), ("second_episode", "A title")):
            item = session.get(Zaisan, library[key])
            assert item is not None
            item.title = title
            item.sort_title = title
            item.release_date = None
            item.air_date = date(2010, 1, 1)
    collection = queries.create_collection(CollectionCreate(name="Franchise"))
    queries.add_collection_membership(
        collection.collection_id,
        CollectionMembershipCreate(expected_revision=1, library_item_id=library["series"]),
    )
    order = queries.create_watch_order(
        collection.collection_id,
        WatchOrderCreate(
            expected_collection_revision=2,
            name="Release",
            kind=WatchOrderKind.CUSTOM,
            generation_mode=WatchOrderGenerationMode.RELEASE,
        ),
    )
    entries = queries.get_watch_order(order.watch_order_id, cursor=None, limit=10).entries.items
    assert [entry.item.id for entry in entries] == [
        library["first_episode"],
        library["second_episode"],
    ]
    preview = queries.preview_watch_order_generation(
        order.watch_order_id,
        WatchOrderGenerationRequest(expected_revision=1, mode=WatchOrderGenerationMode.RELEASE),
    )
    assert not preview.undated_items
    copied = queries.create_watch_order(
        collection.collection_id,
        WatchOrderCreate(
            expected_collection_revision=order.collection_revision,
            name="Alternative",
            kind=WatchOrderKind.RECOMMENDED,
            copy_from_order_id=order.watch_order_id,
        ),
    )
    copied_entries = queries.get_watch_order(
        copied.watch_order_id, cursor=None, limit=10
    ).entries.items
    assert [entry.item.id for entry in copied_entries] == [entry.item.id for entry in entries]
    assert not {entry.id for entry in copied_entries}.intersection(entry.id for entry in entries)
    with pytest.raises(CatalogueNotFoundError):
        queries.create_watch_order(
            collection.collection_id,
            WatchOrderCreate(
                expected_collection_revision=copied.collection_revision,
                name="Invalid copy",
                kind=WatchOrderKind.CUSTOM,
                copy_from_order_id=999_999,
            ),
        )
    assert queries.get_collection(collection.collection_id).watch_order_count == 2


def test_collection_sources_page_unique_descendants_and_warn_on_series_removal(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    library = _library(database, tmp_path)
    queries = _queries(database, tmp_path)
    collection = queries.create_collection(CollectionCreate(name="Franchise"))
    queries.batch_collection_memberships(
        collection.collection_id,
        CollectionMembershipBatchRequest(
            expected_revision=1,
            additions=tuple(
                CollectionMembershipAddition(library_item_id=library[key])
                for key in ("series", "first_episode")
            ),
        ),
    )
    order = queries.create_watch_order(
        collection.collection_id,
        WatchOrderCreate(
            expected_collection_revision=2,
            name="Release",
            kind=WatchOrderKind.CUSTOM,
            generation_mode=WatchOrderGenerationMode.AIR,
        ),
    )
    cursor = None
    ids: list[int] = []
    while True:
        page = queries.list_collection_sources(collection.collection_id, cursor=cursor, limit=2)
        ids.extend(item.id for item in page.items)
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
    assert len(ids) == len(set(ids)) == 5
    assert library["first_episode"] in ids and library["second_episode"] in ids
    assert library["movie"] not in ids
    removed = queries.remove_collection_membership(
        collection.collection_id, library["series"], expected_revision=order.collection_revision
    )
    assert removed.warnings == ("Series: 2 watch-order entries remain.",)
    assert (
        len(queries.get_watch_order(order.watch_order_id, cursor=None, limit=10).entries.items) == 2
    )


def test_simultaneous_order_saves_cannot_share_a_revision(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    queries = _queries(database, tmp_path)
    collection = queries.create_collection(CollectionCreate(name="Franchise"))
    order = queries.create_watch_order(
        collection.collection_id,
        WatchOrderCreate(
            expected_collection_revision=1, name="Original", kind=WatchOrderKind.CUSTOM
        ),
    )
    barrier = Barrier(2)

    def save(name: str) -> bool:
        barrier.wait(timeout=5)
        try:
            queries.update_watch_order(
                order.watch_order_id, WatchOrderUpdate(expected_revision=1, name=name)
            )
        except CatalogueConflictError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(save, ("First", "Second")))
    assert sorted(results) == [False, True]
    assert (
        queries.get_watch_order(order.watch_order_id, cursor=None, limit=1).watch_order.revision
        == 2
    )


def test_membership_lookup_resolves_nearest_ancestor_and_preserves_direct_members(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    ids = _library(database, tmp_path)
    queries = _queries(database, tmp_path)
    collection = queries.create_collection(CollectionCreate(name="Franchise"))
    episode = ids["first_episode"]
    season = database.run_transaction(
        lambda session: session.scalar(select(Zaisan.parent_id).where(Zaisan.id == episode))
    )
    assert season is not None
    revision = collection.revision

    def change(*, add: tuple[int, ...] = (), remove: tuple[int, ...] = ()) -> None:
        nonlocal revision
        result = queries.batch_collection_memberships(
            collection.collection_id,
            CollectionMembershipBatchRequest(
                expected_revision=revision,
                additions=tuple(CollectionMembershipAddition(library_item_id=item) for item in add),
                removals=remove,
            ),
        )
        revision = result.revision

    request = CollectionMembershipLookupRequest(
        library_item_ids=(episode, ids["second_episode"], ids["movie"]), include_ancestors=True
    )
    change(add=(ids["series"],))
    inherited = queries.lookup_collection_memberships(collection.collection_id, request)
    assert inherited.collection.revision == revision
    assert inherited.collection.item_count == 1
    assert not inherited.memberships
    assert {item.ancestor_id for item in inherited.inherited_memberships} == {ids["series"]}
    assert {item.library_item_id for item in inherited.inherited_memberships} == {
        episode,
        ids["second_episode"],
    }
    direct_only = queries.lookup_collection_memberships(
        collection.collection_id, CollectionMembershipLookupRequest(library_item_ids=(episode,))
    )
    assert not direct_only.inherited_memberships

    change(add=(season, episode))
    overlap = queries.lookup_collection_memberships(collection.collection_id, request)
    assert [member.item.id for member in overlap.memberships] == [episode]
    assert {item.ancestor_id for item in overlap.inherited_memberships} == {season}
    change(remove=(episode, season))
    fallback = queries.lookup_collection_memberships(collection.collection_id, request)
    assert {item.ancestor_id for item in fallback.inherited_memberships} == {ids["series"]}
    change(remove=(ids["series"],))
    empty = queries.lookup_collection_memberships(collection.collection_id, request)
    assert not empty.memberships and not empty.inherited_memberships
    assert empty.collection.item_count == 0

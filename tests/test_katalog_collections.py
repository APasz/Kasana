from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from kasana.katalog.api.contracts import (
    CollectionCreate,
    CollectionMembershipCreate,
    CollectionRelationship,
    CollectionUpdate,
    WatchOrderCreate,
    WatchOrderEntriesCreate,
    WatchOrderEntryCreate,
    WatchOrderEntryMove,
    WatchOrderGenerationApplyMode,
    WatchOrderGenerationMode,
    WatchOrderGenerationRequest,
    WatchOrderKind,
)
from kasana.katalog.api.service import (
    CatalogueConflictError,
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
from kasana.katalog.services import create_library_item, create_library_root


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
            relationship=CollectionRelationship.PRIMARY,
        ),
    )
    second = queries.add_collection_membership(
        collection.collection_id,
        CollectionMembershipCreate(
            expected_revision=first.revision,
            library_item_id=library["series"],
            relationship=CollectionRelationship.RELATED,
        ),
    )
    detail = queries.get_collection(collection.collection_id)

    assert detail.revision == second.revision
    assert [(member.item.id, member.relationship) for member in detail.members] == [
        (library["movie"], CollectionRelationship.PRIMARY),
        (library["series"], CollectionRelationship.RELATED),
    ]
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
    assert [(entry.id, entry.relationship) for entry in movie_detail.collections] == [
        (collection.collection_id, None)
    ]


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

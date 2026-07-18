from __future__ import annotations

from collections.abc import Generator
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from kasana.katalog.api.contracts import (
    CollectionCreate,
    CollectionMembershipCreate,
    CollectionRelationship,
    WatchOrderCreate,
    WatchOrderEntryCreate,
    WatchOrderEntryMove,
    WatchOrderGenerationApplyMode,
    WatchOrderGenerationMode,
    WatchOrderGenerationRequest,
    WatchOrderKind,
)
from kasana.katalog.api.service import (
    CatalogConflictError,
    CatalogValidationError,
    KatalogQueryService,
)
from kasana.katalog.database import KatalogDatabase
from kasana.katalog.models import AvailabilityState, KeiroEntry, Zaisan, ZaisanKind
from kasana.katalog.services import create_library_item, create_library_root


@pytest.fixture
def database(tmp_path: Path) -> Generator[KatalogDatabase]:
    database = KatalogDatabase(tmp_path / "katalog.sqlite3")
    database.create_schema()
    yield database
    database.close()


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
    with pytest.raises(CatalogValidationError, match="already"):
        queries.add_collection_membership(
            collection.collection_id,
            CollectionMembershipCreate(
                expected_revision=second.revision,
                library_item_id=library["movie"],
            ),
        )
    with pytest.raises(CatalogConflictError, match="expected revision"):
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

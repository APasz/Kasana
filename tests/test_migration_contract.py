"""Alembic head must create the schema represented by Katalog's ORM metadata."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from kasana.katalog.database import KatalogDatabase
from kasana.katalog.models import Base, PlaybackState, Zaisan, ZaisanKind
from kasana.katalog.services import create_library_item


def _last_insert_id(session: Session) -> int:
    identifier = session.scalar(text("SELECT last_insert_rowid()"))
    assert isinstance(identifier, int)
    return identifier


def _insert_historical_root(
    session: Session, *, path: Path, expected_media_kind: ZaisanKind
) -> int:
    session.execute(
        text(
            "INSERT INTO library_root (path, expected_media_kind, default_tags, enabled) "
            "VALUES (:path, :expected_media_kind, '[]', 1)"
        ),
        {"path": str(path), "expected_media_kind": expected_media_kind.value},
    )
    return _last_insert_id(session)


def _insert_historical_user(session: Session, *, username: str) -> int:
    session.execute(
        text("INSERT INTO user (username, role, is_disabled) VALUES (:username, 'user', 0)"),
        {"username": username},
    )
    return _last_insert_id(session)


def _insert_historical_item(
    session: Session,
    *,
    library_root_id: int,
    item_kind: ZaisanKind,
    title: str,
    parent_id: int | None = None,
    release_year: int | None = None,
    season_number: int | None = None,
    episode_number: int | None = None,
) -> int:
    session.execute(
        text(
            """
            INSERT INTO library_item (
                library_root_id,
                parent_id,
                item_kind,
                title,
                sort_title,
                release_year,
                season_number,
                episode_number,
                tags,
                availability,
                locked_metadata_fields,
                selected_artwork_ids
            ) VALUES (
                :library_root_id,
                :parent_id,
                :item_kind,
                :title,
                :sort_title,
                :release_year,
                :season_number,
                :episode_number,
                '[]',
                'available',
                '[]',
                '{}'
            )
            """
        ),
        {
            "library_root_id": library_root_id,
            "parent_id": parent_id,
            "item_kind": item_kind.value,
            "title": title,
            "sort_title": title,
            "release_year": release_year,
            "season_number": season_number,
            "episode_number": episode_number,
        },
    )
    return _last_insert_id(session)


def test_migration_head_matches_katalog_orm_metadata(tmp_path: Path) -> None:
    database_path = tmp_path / "empty.sqlite3"
    repository_root = Path(__file__).parents[1]
    config = Config(str(repository_root / "alembic.ini"))
    config.set_main_option("script_location", str(repository_root / "alembic"))
    database_url = f"sqlite:///{database_path}"
    config.set_main_option("sqlalchemy.url", database_url)
    head_revision = ScriptDirectory.from_config(config).get_current_head()

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
            context = MigrationContext.configure(
                connection,
                opts={"compare_type": True, "compare_server_default": True},
            )
            differences = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    assert revision == head_revision
    assert differences == []


def test_collection_membership_relationship_migration_discards_legacy_labels(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "catalogue.sqlite3"
    repository_root = Path(__file__).parents[1]
    config = Config(str(repository_root / "alembic.ini"))
    config.set_main_option("script_location", str(repository_root / "alembic"))
    database_url = f"sqlite:///{database_path}"
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "20260915_0032")

    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            root_id = _insert_historical_root(
                session,
                path=tmp_path / "Movies",
                expected_media_kind=ZaisanKind.MOVIE,
            )
            item_id = _insert_historical_item(
                session,
                library_root_id=root_id,
                item_kind=ZaisanKind.MOVIE,
                title="Legacy relationship",
            )
            session.execute(text("INSERT INTO collection (name) VALUES ('Legacy collection')"))
            collection_id = _last_insert_id(session)
            session.execute(
                text(
                    """
                    INSERT INTO collection_membership (collection_id, library_item_id, relationship)
                    VALUES (:collection_id, :item_id, 'primary')
                    """
                ),
                {"collection_id": collection_id, "item_id": item_id},
            )
            session.commit()
    finally:
        engine.dispose()

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        columns = {column["name"] for column in inspector.get_columns("collection_membership")}
        with engine.connect() as connection:
            memberships = tuple(
                connection.execute(
                    text(
                        """
                        SELECT collection_id, library_item_id
                        FROM collection_membership
                        """
                    )
                ).tuples()
            )
    finally:
        engine.dispose()

    assert "relationship" not in columns
    assert memberships == ((collection_id, item_id),)


def test_watch_order_migration_preserves_named_routes_and_entries(tmp_path: Path) -> None:
    database_path = tmp_path / "catalogue.sqlite3"
    repository_root = Path(__file__).parents[1]
    config = Config(str(repository_root / "alembic.ini"))
    config.set_main_option("script_location", str(repository_root / "alembic"))
    database_url = f"sqlite:///{database_path}"
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "20260916_0033")

    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            root_id = _insert_historical_root(
                session,
                path=tmp_path / "Movies",
                expected_media_kind=ZaisanKind.MOVIE,
            )
            item_id = _insert_historical_item(
                session,
                library_root_id=root_id,
                item_kind=ZaisanKind.MOVIE,
                title="Legacy route entry",
            )
            session.execute(text("INSERT INTO collection (name) VALUES ('Legacy routes')"))
            collection_id = _last_insert_id(session)
            session.execute(
                text(
                    """
                    INSERT INTO watch_order (collection_id, name, order_kind, revision)
                    VALUES (:collection_id, 'Air route', 'air', 3)
                    """
                ),
                {"collection_id": collection_id},
            )
            watch_order_id = _last_insert_id(session)
            session.execute(
                text(
                    """
                    INSERT INTO watch_order_entry (watch_order_id, library_item_id, position)
                    VALUES (:watch_order_id, :item_id, 0)
                    """
                ),
                {"watch_order_id": watch_order_id, "item_id": item_id},
            )
            session.execute(
                text(
                    """
                    INSERT INTO watch_order (collection_id, name, order_kind, revision)
                    VALUES (:collection_id, 'Release route', 'custom', 2)
                    """
                ),
                {"collection_id": collection_id},
            )
            release_watch_order_id = _last_insert_id(session)
            session.execute(
                text(
                    """
                    INSERT INTO watch_order_entry (watch_order_id, library_item_id, position)
                    VALUES (:watch_order_id, :item_id, 0)
                    """
                ),
                {"watch_order_id": release_watch_order_id, "item_id": item_id},
            )
            session.execute(
                text(
                    """
                    UPDATE collection
                    SET default_watch_order_id = :watch_order_id
                    WHERE id = :collection_id
                    """
                ),
                {"watch_order_id": watch_order_id, "collection_id": collection_id},
            )
            session.commit()
    finally:
        engine.dispose()

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        columns = {column["name"] for column in inspect(engine).get_columns("watch_order")}
        with engine.connect() as connection:
            routes = tuple(
                connection.execute(
                    text("SELECT id, collection_id, name, revision FROM watch_order ORDER BY id")
                ).tuples()
            )
            defaults = tuple(
                connection.execute(
                    text(
                        """
                        SELECT id, default_watch_order_id
                        FROM collection
                        WHERE id = :collection_id
                        """
                    ),
                    {"collection_id": collection_id},
                ).tuples()
            )
            entries = tuple(
                connection.execute(
                    text(
                        """
                        SELECT watch_order_id, library_item_id, position
                        FROM watch_order_entry
                        ORDER BY watch_order_id, position
                        """
                    )
                ).tuples()
            )
    finally:
        engine.dispose()

    assert "order_kind" not in columns
    assert routes == (
        (watch_order_id, collection_id, "Air route", 3),
        (release_watch_order_id, collection_id, "Release route", 2),
    )
    assert defaults == ((collection_id, watch_order_id),)
    assert entries == (
        (watch_order_id, item_id, 0),
        (release_watch_order_id, item_id, 0),
    )


def test_initial_migration_is_immutable_and_does_not_import_runtime_metadata() -> None:
    migration = (
        Path(__file__).parents[1] / "alembic" / "versions" / "20260722_0013_folded_katalog.py"
    )
    source = migration.read_text(encoding="utf-8")

    assert "from kasana.katalog.models import" not in source
    assert "metadata.create_all" not in source
    assert "metadata.drop_all" not in source
    assert "op.create_table(" in source


def test_remake_migration_allows_distinct_years_and_refuses_an_unsafe_downgrade(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "catalogue.sqlite3"
    repository_root = Path(__file__).parents[1]
    config = Config(str(repository_root / "alembic.ini"))
    config.set_main_option("script_location", str(repository_root / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(config, "20260829_0025")

    database = KatalogDatabase(database_path)
    try:

        def seed(session: Session) -> None:
            root_id = _insert_historical_root(
                session,
                path=tmp_path / "Movies",
                expected_media_kind=ZaisanKind.MOVIE,
            )
            _insert_historical_item(
                session,
                library_root_id=root_id,
                item_kind=ZaisanKind.MOVIE,
                title="Ghostbusters",
                release_year=1984,
            )

        database.run_transaction(seed)
    finally:
        database.close()

    command.upgrade(config, "head")
    database = KatalogDatabase(database_path)
    try:

        def add_remake_item(session: Session) -> None:
            root = session.scalar(select(Zaisan.library_root_id).limit(1))
            assert root is not None
            create_library_item(
                session,
                library_root_id=root,
                item_kind=ZaisanKind.MOVIE,
                title="Ghostbusters",
                release_year=2016,
            )

        database.run_transaction(add_remake_item)
        assert database.run_transaction(
            lambda session: tuple(
                session.scalars(
                    select(Zaisan.release_year)
                    .where(Zaisan.item_kind == ZaisanKind.MOVIE)
                    .order_by(Zaisan.release_year)
                )
            )
        ) == (1984, 2016)
        with pytest.raises(IntegrityError):
            database.run_transaction(add_remake_item)
    finally:
        database.close()

    with pytest.raises(RuntimeError, match="same-titled movie remakes"):
        command.downgrade(config, "20260829_0025")


def test_completion_rollup_migration_accounts_for_direct_series_specials(tmp_path: Path) -> None:
    database_path = tmp_path / "catalogue.sqlite3"
    repository_root = Path(__file__).parents[1]
    config = Config(str(repository_root / "alembic.ini"))
    config.set_main_option("script_location", str(repository_root / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    command.upgrade(config, "20260728_0021")

    database = KatalogDatabase(database_path)
    try:
        with database.transaction() as session:
            root_id = _insert_historical_root(
                session,
                path=tmp_path / "library",
                expected_media_kind=ZaisanKind.SERIES,
            )
            user_id = _insert_historical_user(session, username="viewer")
            series_id = _insert_historical_item(
                session,
                library_root_id=root_id,
                item_kind=ZaisanKind.SERIES,
                title="Example Show",
            )
            season_id = _insert_historical_item(
                session,
                library_root_id=root_id,
                item_kind=ZaisanKind.SEASON,
                parent_id=series_id,
                season_number=1,
                title="Season 1",
            )
            episodes = tuple(
                _insert_historical_item(
                    session,
                    library_root_id=root_id,
                    item_kind=ZaisanKind.EPISODE,
                    parent_id=season_id,
                    season_number=1,
                    episode_number=episode_number,
                    title=f"Episode {episode_number}",
                )
                for episode_number in (1, 2)
            )
            extra_id = _insert_historical_item(
                session,
                library_root_id=root_id,
                item_kind=ZaisanKind.EXTRA,
                parent_id=season_id,
                title="Interview",
            )
            direct_special_id = _insert_historical_item(
                session,
                library_root_id=root_id,
                item_kind=ZaisanKind.SPECIAL,
                parent_id=series_id,
                title="Unwatched special",
            )
            special_only_series_id = _insert_historical_item(
                session,
                library_root_id=root_id,
                item_kind=ZaisanKind.SERIES,
                title="Specials only",
            )
            special_only_id = _insert_historical_item(
                session,
                library_root_id=root_id,
                item_kind=ZaisanKind.SPECIAL,
                parent_id=special_only_series_id,
                title="Watched special",
            )
            for episode_id in (*episodes, special_only_id):
                session.execute(
                    text(
                        """
                        INSERT INTO playback_state (
                            user_id,
                            library_item_id,
                            position_seconds,
                            duration_seconds,
                            completed,
                            play_count,
                            last_played_at
                        ) VALUES (:user_id, :library_item_id, 1.0, 1.0, 1, 1, :last_played_at)
                        """
                    ),
                    {
                        "user_id": user_id,
                        "library_item_id": episode_id,
                        "last_played_at": datetime.now(UTC).isoformat(),
                    },
                )
            identifiers = (
                user_id,
                season_id,
                series_id,
                direct_special_id,
                special_only_series_id,
                extra_id,
            )
    finally:
        database.close()

    command.upgrade(config, "head")

    database = KatalogDatabase(database_path)
    try:
        with database.transaction() as session:
            (
                user_id,
                season_id,
                series_id,
                direct_special_id,
                special_only_series_id,
                extra_id,
            ) = identifiers
            completed_item_ids = {
                state.library_item_id
                for state in session.query(PlaybackState).filter_by(user_id=user_id, completed=True)
            }
    finally:
        database.close()

    assert {season_id, special_only_series_id}.issubset(completed_item_ids)
    assert series_id not in completed_item_ids
    assert direct_special_id not in completed_item_ids
    assert extra_id not in completed_item_ids

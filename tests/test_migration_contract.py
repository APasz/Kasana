"""Alembic head must create the schema represented by Katalog's ORM metadata."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from kasana.katalog.models import Base


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

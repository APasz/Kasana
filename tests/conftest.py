"""Shared pytest fixtures for Katalog's database-backed contracts."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

from kasana.katalog.database import KatalogDatabase


@pytest.fixture
def database(tmp_path: Path) -> Generator[KatalogDatabase]:
    """Provide an isolated Katalog database with the current schema installed."""
    katalog_database = KatalogDatabase(tmp_path / "katalog.sqlite3")
    katalog_database.create_schema()
    yield katalog_database
    katalog_database.close()

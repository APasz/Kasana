"""SQLite lifecycle and explicit transaction boundaries for Katalog."""

import sqlite3
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from sqlite3 import Cursor
from typing import TypeVar

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from kasana.katalog.models import Base
from kasana.katalog.numerals import natural_sort_key

Result = TypeVar("Result")


class KatalogDatabase:
    """Owns SQLite configuration and transaction scopes for Katalog worker code."""

    def __init__(self, database_path: Path, *, busy_timeout_ms: int = 5_000) -> None:
        if not database_path.is_absolute():
            msg = "The SQLite database path must be absolute."
            raise ValueError(msg)
        if busy_timeout_ms <= 0:
            msg = "The SQLite busy timeout must be positive."
            raise ValueError(msg)
        self.database_path = database_path

        self.engine: Engine = create_engine(
            f"sqlite:///{database_path}",
            connect_args={"check_same_thread": False},
            poolclass=NullPool,
        )
        self.session_factory: sessionmaker[Session] = sessionmaker(
            self.engine, expire_on_commit=False
        )
        self._configure_sqlite(busy_timeout_ms)

    def _configure_sqlite(self, busy_timeout_ms: int) -> None:
        def configure_connection(connection: sqlite3.Connection, _: object) -> None:
            cursor: Cursor = connection.cursor()
            cursor.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.close()
            connection.create_function("natural_sort_key", 1, natural_sort_key, deterministic=True)

        event.listen(self.engine, "connect", configure_connection)
        self._ensure_wal_journal_mode()

    def _ensure_wal_journal_mode(self) -> None:
        """Persist WAL mode once without demanding an exclusive lock per request."""

        with self.engine.connect() as connection:
            journal_mode = str(connection.exec_driver_sql("PRAGMA journal_mode").scalar_one())
            if journal_mode.casefold() != "wal":
                connection.exec_driver_sql("PRAGMA journal_mode = WAL")

    def create_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    @contextmanager
    def transaction(self) -> Generator[Session]:
        with self.session_factory.begin() as session:
            yield session

    def run_transaction(self, operation: Callable[[Session], Result]) -> Result:
        with self.transaction() as session:
            return operation(session)

    def backup_to(self, destination: Path) -> None:
        """Create a consistent SQLite backup without touching media files."""

        if not destination.is_absolute():
            msg = "The SQLite backup path must be absolute."
            raise ValueError(msg)
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(self.database_path)
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()

    def close(self) -> None:
        self.engine.dispose()

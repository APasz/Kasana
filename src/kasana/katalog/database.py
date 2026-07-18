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
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
            cursor.close()

        event.listen(self.engine, "connect", configure_connection)

    def create_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    @contextmanager
    def transaction(self) -> Generator[Session]:
        with self.session_factory.begin() as session:
            yield session

    def run_transaction(self, operation: Callable[[Session], Result]) -> Result:
        with self.transaction() as session:
            return operation(session)

    def close(self) -> None:
        self.engine.dispose()

"""Portable JSON snapshots for Katalog's durable local state."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import shutil
import sqlite3
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile, mkdtemp
from typing import cast

from kasana.katalog.user_configuration import UserConfiguration, UserConfigurationStore
from kasana.shared.concurrency import run_blocking

_BACKUP_FORMAT = "kasana-katalog-backup"
_BACKUP_VERSION = 1
_BINARY_VALUE_KEY = "__kasana_binary_base64__"
_LOGGER = logging.getLogger(__name__)

type SQLiteValue = str | int | float | bytes | None
type JsonObject = dict[str, object]


class BackupError(RuntimeError):
    """A JSON backup cannot be created, validated, or restored safely."""


def create_json_backup(
    database_path: Path,
    destination: Path,
    *,
    user_configuration_directory: Path,
) -> None:
    """Atomically write a consistent SQLite and profile snapshot as JSON.

    Artwork files are intentionally not copied: their database metadata is
    retained and the configured artwork cache can be repopulated from source.
    """

    _require_absolute_path(database_path, "SQLite database")
    _require_absolute_path(destination, "JSON backup")
    _require_absolute_path(user_configuration_directory, "User configuration directory")
    if not database_path.is_file():
        raise BackupError(f"SQLite database {database_path} does not exist.")

    document: JsonObject = {
        "format": _BACKUP_FORMAT,
        "version": _BACKUP_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "database": _database_snapshot(database_path),
        "user_configurations": _user_configuration_snapshot(user_configuration_directory),
    }
    _write_json_atomically(destination, document)


def restore_json_backup(
    source: Path,
    database_path: Path,
    *,
    user_configuration_directory: Path,
) -> None:
    """Replace the local database and profile documents from a validated snapshot.

    Callers must ensure Katalog is stopped first so no other process can retain
    SQLite connections while the database file is atomically replaced.
    """

    _require_absolute_path(source, "JSON backup")
    _require_absolute_path(database_path, "SQLite database")
    _require_absolute_path(user_configuration_directory, "User configuration directory")
    snapshot = _read_snapshot(source)
    temporary_database = _build_restored_database(database_path, snapshot.database)
    temporary_configurations = _build_restored_user_configurations(
        user_configuration_directory, snapshot.user_configurations
    )
    previous_database: Path | None = None
    database_replacement_started = False
    try:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        previous_database = _move_existing_database(database_path)
        database_replacement_started = True
        os.replace(temporary_database, database_path)
        _replace_user_configuration_directory(
            user_configuration_directory, temporary_configurations
        )
        _remove_sqlite_sidecars(database_path)
    except OSError as error:
        if database_replacement_started:
            try:
                _rollback_database_replacement(database_path, previous_database)
            except OSError as rollback_error:
                raise BackupError(
                    f"Unable to replace restored data: {error}; "
                    f"database rollback also failed: {rollback_error}"
                ) from error
        raise BackupError(f"Unable to replace restored data: {error}") from error
    finally:
        _remove_path(temporary_database)
        _remove_path(temporary_configurations)
        if previous_database is not None:
            _remove_path(previous_database)


class JsonBackupScheduler:
    """Runs one resilient JSON backup task at the configured fixed interval."""

    def __init__(
        self,
        database_path: Path,
        destination: Path,
        *,
        user_configuration_directory: Path,
        interval: timedelta,
    ) -> None:
        if interval <= timedelta():
            raise ValueError("The JSON backup interval must be positive.")
        self._database_path = database_path
        self._destination = destination
        self._user_configuration_directory = user_configuration_directory
        self._interval = interval
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="katalog-json-backup")

    async def close(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        delay = _backup_delay(self._destination, self._interval)
        while True:
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                await run_blocking(
                    create_json_backup,
                    self._database_path,
                    self._destination,
                    user_configuration_directory=self._user_configuration_directory,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception("Scheduled Katalog JSON backup failed.")
            delay = self._interval.total_seconds()


@dataclass(frozen=True)
class _Snapshot:
    database: _DatabaseSnapshot
    user_configurations: tuple[_UserConfiguration, ...]


@dataclass(frozen=True)
class _DatabaseSnapshot:
    schema_objects: tuple[_SchemaObject, ...]
    tables: tuple[_TableSnapshot, ...]


@dataclass(frozen=True)
class _SchemaObject:
    object_type: str
    name: str
    sql: str


@dataclass(frozen=True)
class _TableSnapshot:
    name: str
    columns: tuple[str, ...]
    rows: tuple[tuple[SQLiteValue, ...], ...]


@dataclass(frozen=True)
class _UserConfiguration:
    user_id: int
    configuration: UserConfiguration


def _database_snapshot(database_path: Path) -> JsonObject:
    source = sqlite3.connect(database_path)
    consistent_copy = sqlite3.connect(":memory:")
    try:
        source.backup(consistent_copy)
        schema_rows = consistent_copy.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
            "AND type IN ('table', 'index', 'trigger', 'view') "
            "ORDER BY CASE type WHEN 'table' THEN 0 WHEN 'view' THEN 1 "
            "WHEN 'index' THEN 2 ELSE 3 END, name"
        ).fetchall()
        schema_objects: list[JsonObject] = []
        tables: list[JsonObject] = []
        for object_type, name, sql in schema_rows:
            if (
                not isinstance(object_type, str)
                or not isinstance(name, str)
                or not isinstance(sql, str)
            ):
                raise BackupError("SQLite schema contains an unsupported object.")
            schema_objects.append({"type": object_type, "name": name, "sql": sql})
            if object_type != "table":
                continue
            columns = tuple(
                column[1]
                for column in consistent_copy.execute(f"PRAGMA table_info({_quote(name)})")
            )
            if not all(isinstance(column, str) for column in columns):
                raise BackupError(f"SQLite table {name} has an invalid column name.")
            rows = consistent_copy.execute(f"SELECT * FROM {_quote(name)}").fetchall()
            tables.append(
                {
                    "name": name,
                    "columns": list(columns),
                    "rows": [
                        [_encode_sqlite_value(cast(SQLiteValue, value)) for value in row]
                        for row in rows
                    ],
                }
            )
        return {"schema_objects": schema_objects, "tables": tables}
    except sqlite3.Error as error:
        raise BackupError(f"Unable to read SQLite database: {error}") from error
    finally:
        consistent_copy.close()
        source.close()


def _user_configuration_snapshot(directory: Path) -> list[JsonObject]:
    store = UserConfigurationStore(directory)
    return [
        {
            "user_id": user_id,
            "configuration": configuration.model_dump(mode="json"),
        }
        for user_id, configuration in store.configured_users()
    ]


def _write_json_atomically(destination: Path, document: JsonObject) -> None:
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent, prefix=".backup-", delete=False
        ) as temporary_file:
            json.dump(document, temporary_file, indent=2, sort_keys=True, ensure_ascii=False)
            temporary_file.write("\n")
            temporary_path = Path(temporary_file.name)
        temporary_path.chmod(0o600)
        temporary_path.replace(destination)
    except (OSError, TypeError) as error:
        raise BackupError(f"Unable to write JSON backup {destination}: {error}") from error


def _read_snapshot(source: Path) -> _Snapshot:
    try:
        raw_document = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise BackupError(f"JSON backup {source} does not exist.") from error
    except (OSError, json.JSONDecodeError) as error:
        raise BackupError(f"Unable to read JSON backup {source}: {error}") from error
    document = _object(raw_document, "Backup")
    if document.get("format") != _BACKUP_FORMAT or document.get("version") != _BACKUP_VERSION:
        raise BackupError("Unsupported Katalog JSON backup format or version.")
    database_document = _object(document.get("database"), "Backup database")
    schema_object_values = _array(database_document.get("schema_objects"), "schema_objects")
    schema_objects = tuple(_schema_object(value) for value in schema_object_values)
    table_values = _array(database_document.get("tables"), "tables")
    tables = tuple(_table_snapshot(value) for value in table_values)
    if not schema_objects or not tables:
        raise BackupError("Backup must contain a SQLite schema and at least one table.")
    configurations = tuple(
        _user_configuration(value)
        for value in _array(document.get("user_configurations"), "user_configurations")
    )
    _validate_snapshot(_DatabaseSnapshot(schema_objects, tables), configurations)
    return _Snapshot(_DatabaseSnapshot(schema_objects, tables), configurations)


def _schema_object(value: object) -> _SchemaObject:
    document = _object(value, "Schema object")
    object_type = _string(document.get("type"), "Schema object type")
    if object_type not in {"table", "index", "trigger", "view"}:
        raise BackupError(f"Unsupported schema object type {object_type!r}.")
    return _SchemaObject(
        object_type,
        _identifier(_string(document.get("name"), "Schema object name")),
        _string(document.get("sql"), "Schema object SQL"),
    )


def _table_snapshot(value: object) -> _TableSnapshot:
    document = _object(value, "Table")
    name = _identifier(_string(document.get("name"), "Table name"))
    columns = tuple(
        _identifier(_string(column, f"Columns for table {name}"))
        for column in _array(document.get("columns"), f"Columns for table {name}")
    )
    if not columns or len(columns) != len(set(columns)):
        raise BackupError(f"Table {name} must have unique columns.")
    rows: list[tuple[SQLiteValue, ...]] = []
    for row in _array(document.get("rows"), f"Rows for table {name}"):
        values = _array(row, f"Row for table {name}")
        if len(values) != len(columns):
            raise BackupError(f"A row in table {name} has the wrong number of values.")
        rows.append(tuple(_decode_sqlite_value(item) for item in values))
    return _TableSnapshot(name, columns, tuple(rows))


def _user_configuration(value: object) -> _UserConfiguration:
    document = _object(value, "User configuration")
    user_id = document.get("user_id")
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        raise BackupError("User configuration IDs must be positive integers.")
    try:
        configuration = UserConfiguration.model_validate(
            _object(document.get("configuration"), "User configuration document")
        )
    except ValueError as error:
        raise BackupError(f"Invalid user configuration for ID {user_id}: {error}") from error
    return _UserConfiguration(user_id, configuration)


def _validate_snapshot(
    database: _DatabaseSnapshot, configurations: tuple[_UserConfiguration, ...]
) -> None:
    table_names = {table.name for table in database.tables}
    schema_tables = {
        schema.name for schema in database.schema_objects if schema.object_type == "table"
    }
    if table_names != schema_tables:
        raise BackupError("Backup table data does not match its SQLite schema.")
    if len(table_names) != len(database.tables):
        raise BackupError("Backup contains duplicate table data.")
    if len({entry.user_id for entry in configurations}) != len(configurations):
        raise BackupError("Backup contains duplicate user configuration IDs.")


def _build_restored_database(destination: Path, snapshot: _DatabaseSnapshot) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        dir=destination.parent,
        prefix=".restore-",
        suffix=".sqlite3",
        delete=False,
    ) as temporary_file:
        temporary_path = Path(temporary_file.name)
    database = sqlite3.connect(temporary_path)
    try:
        database.execute("PRAGMA foreign_keys = OFF")
        for schema_object in snapshot.schema_objects:
            database.execute(schema_object.sql)
        for table in snapshot.tables:
            placeholders = ", ".join("?" for _ in table.columns)
            columns = ", ".join(_quote(column) for column in table.columns)
            statement = f"INSERT INTO {_quote(table.name)} ({columns}) VALUES ({placeholders})"
            database.executemany(statement, table.rows)
        database.execute("PRAGMA foreign_keys = ON")
        foreign_key_errors = database.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise BackupError("Backup data fails SQLite foreign-key validation.")
        integrity = database.execute("PRAGMA integrity_check").fetchone()
        if integrity != ("ok",):
            raise BackupError("Backup data fails SQLite integrity validation.")
        database.commit()
        return temporary_path
    except (sqlite3.Error, BackupError) as error:
        database.rollback()
        _remove_path(temporary_path)
        raise BackupError(f"Unable to rebuild SQLite database: {error}") from error
    finally:
        database.close()


def _build_restored_user_configurations(
    destination: Path, configurations: tuple[_UserConfiguration, ...]
) -> Path:
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    staging_directory = Path(
        mkdtemp(prefix=f".{destination.name}-restore-", dir=destination.parent)
    )
    try:
        store = UserConfigurationStore(staging_directory)
        for entry in configurations:
            store.save(entry.user_id, entry.configuration)
        return staging_directory
    except OSError as error:
        _remove_path(staging_directory)
        raise BackupError(f"Unable to stage user configurations: {error}") from error


def _move_existing_database(destination: Path) -> Path | None:
    if not destination.exists():
        return None
    with NamedTemporaryFile(
        dir=destination.parent,
        prefix=f".{destination.name}-previous-",
        suffix=".sqlite3",
        delete=False,
    ) as temporary_file:
        previous_database = Path(temporary_file.name)
    previous_database.unlink()
    os.replace(destination, previous_database)
    return previous_database


def _rollback_database_replacement(destination: Path, previous_database: Path | None) -> None:
    if previous_database is None:
        _remove_path(destination)
        return
    _remove_path(destination)
    os.replace(previous_database, destination)


def _replace_user_configuration_directory(destination: Path, staged_directory: Path) -> None:
    if not destination.exists():
        os.replace(staged_directory, destination)
        return
    previous_directory = Path(
        mkdtemp(prefix=f".{destination.name}-previous-", dir=destination.parent)
    )
    previous_directory.rmdir()
    try:
        os.replace(destination, previous_directory)
        os.replace(staged_directory, destination)
    except OSError:
        if previous_directory.exists() and not destination.exists():
            os.replace(previous_directory, destination)
        raise
    _remove_path(previous_directory)


def _backup_delay(destination: Path, interval: timedelta) -> float:
    try:
        age = datetime.now(UTC).timestamp() - destination.stat().st_mtime
    except FileNotFoundError:
        return 0.0
    return max(0.0, interval.total_seconds() - age)


def _encode_sqlite_value(value: SQLiteValue) -> object:
    if isinstance(value, bytes):
        return {_BINARY_VALUE_KEY: base64.b64encode(value).decode("ascii")}
    return value


def _decode_sqlite_value(value: object) -> SQLiteValue:
    if value is None or (isinstance(value, str | int | float) and not isinstance(value, bool)):
        return value
    if isinstance(value, dict):
        binary_value = _object(cast(object, value), "Binary SQLite value")
        if set(binary_value) != {_BINARY_VALUE_KEY}:
            raise BackupError("Backup contains an unsupported SQLite value.")
        encoded = binary_value[_BINARY_VALUE_KEY]
        if isinstance(encoded, str):
            try:
                return base64.b64decode(encoded, validate=True)
            except ValueError as error:
                raise BackupError("Backup contains invalid binary data.") from error
    raise BackupError("Backup contains an unsupported SQLite value.")


def _object(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise BackupError(f"{label} must be a JSON object.")
    document = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in document):
        raise BackupError(f"{label} must be a JSON object.")
    return cast(Mapping[str, object], document)


def _array(value: object, label: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise BackupError(f"{label} must be a JSON array.")
    return cast(list[object], value)


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise BackupError(f"{label} must be a non-empty string.")
    return value


def _identifier(value: str) -> str:
    if "\x00" in value:
        raise BackupError("SQLite identifiers cannot contain null bytes.")
    return value


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _require_absolute_path(path: Path, label: str) -> None:
    if not path.is_absolute():
        raise ValueError(f"The {label} path must be absolute.")


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        with suppress(FileNotFoundError):
            path.unlink()


def _remove_sqlite_sidecars(database_path: Path) -> None:
    """Discard WAL state from the replaced database, never from its backup source."""

    for suffix in ("-shm", "-wal"):
        _remove_path(database_path.with_name(f"{database_path.name}{suffix}"))

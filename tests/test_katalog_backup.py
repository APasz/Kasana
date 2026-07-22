"""Round-trip coverage for Katalog's portable JSON backups."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select
from typer.testing import CliRunner

from kasana.katalog import backup as backup_module
from kasana.katalog.backup import BackupError, create_json_backup, restore_json_backup
from kasana.katalog.cli import app as katalog_cli
from kasana.katalog.database import KatalogDatabase
from kasana.katalog.models import Kura, Zaisan, ZaisanKind
from kasana.katalog.services import create_library_item, create_library_root
from kasana.katalog.user_configuration import UserConfiguration, UserConfigurationStore


def test_json_backup_restores_database_and_profile_configuration(tmp_path: Path) -> None:
    database_path = (tmp_path / "catalogue.sqlite3").resolve()
    backup_path = (tmp_path / "catalogue.backup.json").resolve()
    users_directory = (tmp_path / "users").resolve()
    database = KatalogDatabase(database_path)
    database.create_schema()
    try:
        with database.transaction() as session:
            root = create_library_root(
                session,
                path=tmp_path / "Movies",
                expected_media_kind=ZaisanKind.MOVIE,
            )
            create_library_item(
                session,
                library_root_id=root.id,
                item_kind=ZaisanKind.MOVIE,
                title="A backup film",
                release_year=2024,
            )
        UserConfigurationStore(users_directory).save(
            7,
            UserConfiguration(username="owner", name="Owner", pin="4242", accent_colour="#123456"),
        )
        create_json_backup(
            database_path,
            backup_path,
            user_configuration_directory=users_directory,
        )
    finally:
        database.close()

    document = json.loads(backup_path.read_text(encoding="utf-8"))
    assert document["format"] == "kasana-katalog-backup"
    assert document["version"] == 1
    assert document["user_configurations"][0]["user_id"] == 7

    connection = sqlite3.connect(database_path)
    try:
        connection.execute("DELETE FROM library_item")
        connection.commit()
    finally:
        connection.close()
    UserConfigurationStore(users_directory).save(
        7, UserConfiguration(username="changed", accent_colour="#654321")
    )

    restore_json_backup(
        backup_path,
        database_path,
        user_configuration_directory=users_directory,
    )

    restored = KatalogDatabase(database_path)
    try:
        item_count = restored.run_transaction(
            lambda session: session.scalar(select(func.count(Zaisan.id)))
        )
        root_count = restored.run_transaction(
            lambda session: session.scalar(select(func.count(Kura.id)))
        )
        assert item_count == 1
        assert root_count == 1
    finally:
        restored.close()
    assert UserConfigurationStore(users_directory).load(7) == UserConfiguration(
        username="owner",
        name="Owner",
        pin="4242",
        accent_colour="#123456",
    )


def test_invalid_json_backup_does_not_replace_database(tmp_path: Path) -> None:
    database_path = (tmp_path / "catalogue.sqlite3").resolve()
    backup_path = (tmp_path / "invalid.json").resolve()
    users_directory = (tmp_path / "users").resolve()
    database = KatalogDatabase(database_path)
    database.create_schema()
    database.close()
    original = database_path.read_bytes()
    backup_path.write_text('{"format":"wrong"}', encoding="utf-8")

    with pytest.raises(BackupError, match="Unsupported"):
        restore_json_backup(
            backup_path,
            database_path,
            user_configuration_directory=users_directory,
        )

    assert database_path.read_bytes() == original


def test_restore_failure_rolls_back_database_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = (tmp_path / "catalogue.sqlite3").resolve()
    backup_path = (tmp_path / "catalogue.backup.json").resolve()
    users_directory = (tmp_path / "users").resolve()
    database = KatalogDatabase(database_path)
    database.create_schema()
    try:
        with database.transaction() as session:
            create_library_root(
                session,
                path=tmp_path / "Backup",
                expected_media_kind=ZaisanKind.MOVIE,
            )
        UserConfigurationStore(users_directory).save(
            7, UserConfiguration(username="backup", accent_colour="#123456")
        )
        create_json_backup(
            database_path,
            backup_path,
            user_configuration_directory=users_directory,
        )

        with database.transaction() as session:
            session.execute(delete(Zaisan))
            session.execute(delete(Kura))
            create_library_root(
                session,
                path=tmp_path / "Current",
                expected_media_kind=ZaisanKind.MOVIE,
            )
        UserConfigurationStore(users_directory).save(
            7, UserConfiguration(username="current", accent_colour="#654321")
        )
    finally:
        database.close()

    def fail_configuration_replacement(destination: Path, staged_directory: Path) -> None:
        raise OSError("blocked")

    monkeypatch.setattr(
        backup_module,
        "_replace_user_configuration_directory",
        fail_configuration_replacement,
    )

    with pytest.raises(BackupError, match="Unable to replace restored data"):
        backup_module.restore_json_backup(
            backup_path,
            database_path,
            user_configuration_directory=users_directory,
        )

    restored = KatalogDatabase(database_path)
    try:
        paths = restored.run_transaction(
            lambda session: tuple(session.scalars(select(Kura.path).order_by(Kura.path)))
        )
    finally:
        restored.close()
    assert paths == (str(tmp_path / "Current"),)
    assert UserConfigurationStore(users_directory).load(7) == UserConfiguration(
        username="current",
        accent_colour="#654321",
    )


def test_cli_backup_and_restore_require_explicit_confirmation(tmp_path: Path) -> None:
    database_path = tmp_path / "catalogue.sqlite3"
    backup_path = tmp_path / "catalogue.backup.json"
    environment = {
        "KASANA_KATALOG_DATABASE_PATH": str(database_path),
        "KASANA_KATALOG_USER_CONFIGURATION_DIRECTORY": str(tmp_path / "users"),
    }
    runner = CliRunner()

    initialise = runner.invoke(katalog_cli.app, ["database", "initialise"], env=environment)
    assert initialise.exit_code == 0, initialise.output
    backup = runner.invoke(
        katalog_cli.app,
        ["--json", "database", "backup", str(backup_path)],
        env=environment,
    )
    assert backup.exit_code == 0, backup.output
    assert json.loads(backup.output) == {"backup_path": str(backup_path.resolve())}

    blocked = runner.invoke(
        katalog_cli.app,
        ["database", "restore", str(backup_path)],
        env=environment,
    )
    assert blocked.exit_code == 2
    assert "--yes" in blocked.output
    restored = runner.invoke(
        katalog_cli.app,
        ["--json", "database", "restore", str(backup_path), "--yes"],
        env=environment,
    )
    assert restored.exit_code == 0, restored.output
    assert json.loads(restored.output) == {"backup_path": str(backup_path.resolve())}

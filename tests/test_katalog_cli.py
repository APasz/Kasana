from __future__ import annotations

import json
from pathlib import Path

from pytest import MonkeyPatch
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from kasana.katalog.cli import app as katalog_cli
from kasana.katalog.cli import scanning as scanning_cli
from kasana.katalog.database import KatalogDatabase
from kasana.katalog.models import Zaisan, ZaisanKind
from kasana.katalog.scanning import IncrementalScanner, ScanResult
from kasana.katalog.services import attach_media_file, create_library_item, create_library_root


def _environment(database_path: Path) -> dict[str, str]:
    return {
        "KASANA_KATALOG_DATABASE_PATH": str(database_path),
        "KASANA_KATALOG_USER_CONFIGURATION_DIRECTORY": str(database_path.parent / "users"),
    }


def _initialise(runner: CliRunner, environment: dict[str, str]) -> None:
    result = runner.invoke(katalog_cli.app, ["database", "initialise"], env=environment)
    assert result.exit_code == 0, result.output


def _create_movie(database_path: Path, library_path: Path) -> int:
    database = KatalogDatabase(database_path.resolve())
    try:

        def create(session: Session) -> int:
            root = create_library_root(
                session,
                path=library_path,
                expected_media_kind=ZaisanKind.MOVIE,
            )
            return create_library_item(
                session,
                library_root_id=root.id,
                item_kind=ZaisanKind.MOVIE,
                title="CLI Film",
                release_year=2020,
            ).id

        return database.run_transaction(create)
    finally:
        database.close()


def test_database_and_library_commands_emit_stable_json(tmp_path: Path) -> None:
    runner = CliRunner()
    environment = _environment(tmp_path / "catalogue.sqlite3")
    library_path = tmp_path / "offline-library"

    _initialise(runner, environment)
    user = runner.invoke(
        katalog_cli.app,
        ["--json", "user", "create", "owner", "--display-name", "Owner"],
        env=environment,
    )
    assert user.exit_code == 0, user.output
    assert json.loads(user.output) == {"display_name": "Owner", "id": 1, "username": "owner"}

    users = runner.invoke(katalog_cli.app, ["--json", "user", "list"], env=environment)
    assert users.exit_code == 0, users.output
    assert json.loads(users.output) == [{"display_name": "Owner", "id": 1, "username": "owner"}]

    current = runner.invoke(katalog_cli.app, ["--json", "database", "current"], env=environment)
    assert current.exit_code == 0, current.output
    assert json.loads(current.output) == {"revision": "20260722_0013"}

    added = runner.invoke(
        katalog_cli.app,
        [
            "--json",
            "library",
            "add",
            str(library_path),
            "--expected-kind",
            "movie",
            "--tag",
            "anime",
            "--display-name",
            "Films",
        ],
        env=environment,
    )
    assert added.exit_code == 0, added.output
    root = json.loads(added.output)
    assert root == {
        "default_tags": ["anime"],
        "display_name": "Films",
        "enabled": True,
        "expected_kind": "movie",
        "id": 1,
        "last_scan_completed_at": None,
        "path": str(library_path.resolve()),
    }

    updated = runner.invoke(
        katalog_cli.app,
        ["--json", "library", "update", "1", "--disabled", "--display-name", "Archive"],
        env=environment,
    )
    assert updated.exit_code == 0, updated.output
    assert json.loads(updated.output)["enabled"] is False

    listed = runner.invoke(katalog_cli.app, ["--json", "library", "list"], env=environment)
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.output)[0]["display_name"] == "Archive"

    removed = runner.invoke(katalog_cli.app, ["--json", "library", "remove", "1"], env=environment)
    assert removed.exit_code == 0, removed.output
    assert json.loads(removed.output) == {"removed_root_id": 1}


def test_database_upgrade_creates_configured_database_parent_directory(tmp_path: Path) -> None:
    runner = CliRunner()
    database_path = tmp_path / "missing" / "nested" / "catalogue.sqlite3"

    result = runner.invoke(
        katalog_cli.app, ["database", "upgrade"], env=_environment(database_path)
    )

    assert result.exit_code == 0, result.output
    assert database_path.is_file()


def test_item_discovery_commands_emit_playback_ids(tmp_path: Path) -> None:
    runner = CliRunner()
    environment = _environment(tmp_path / "catalogue.sqlite3")
    _initialise(runner, environment)
    movie_id = _create_movie(tmp_path / "catalogue.sqlite3", tmp_path / "item-library")

    items = runner.invoke(katalog_cli.app, ["--json", "item", "search", "CLI"], env=environment)
    assert items.exit_code == 0, items.output
    assert json.loads(items.output) == [
        {
            "availability": "available",
            "id": movie_id,
            "kind": "movie",
            "title": "CLI Film",
            "year": 2020,
        }
    ]

    human_items = runner.invoke(katalog_cli.app, ["item", "search", "CLI"], env=environment)
    assert human_items.exit_code == 0, human_items.output
    assert "Library search" in human_items.output
    assert "CLI Film" in human_items.output
    assert "Availability" in human_items.output

    item = runner.invoke(
        katalog_cli.app, ["--json", "item", "show", str(movie_id)], env=environment
    )
    assert item.exit_code == 0, item.output
    assert json.loads(item.output)["title"] == "CLI Film"


def test_hierarchy_repair_command_defaults_to_dry_run_and_requires_explicit_apply(
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    database_path = tmp_path / "catalogue.sqlite3"
    environment = _environment(database_path)
    _initialise(runner, environment)
    library_path = tmp_path / "Movies"
    database = KatalogDatabase(database_path.resolve())
    try:

        def create(session: Session) -> int:
            root = create_library_root(
                session,
                path=library_path,
                expected_media_kind=ZaisanKind.MOVIE,
            )
            malformed = create_library_item(
                session,
                library_root_id=root.id,
                item_kind=ZaisanKind.MOVIE,
                title="2000s",
            )
            attach_media_file(
                session,
                library_item_id=malformed.id,
                absolute_path=library_path / "2000s" / "CLI Film.mkv",
                size_bytes=1,
                mtime_ns=1,
                container="matroska",
            )
            return malformed.id

        malformed_id = database.run_transaction(create)
    finally:
        database.close()

    preview = runner.invoke(
        katalog_cli.app,
        ["repair", "hierarchy", "--dry-run", "--json"],
        env=environment,
    )
    assert preview.exit_code == 0, preview.output
    assert json.loads(preview.output)["applied"] is False

    blocked = runner.invoke(
        katalog_cli.app,
        ["repair", "hierarchy", "--apply"],
        env=environment,
    )
    assert blocked.exit_code == 2
    assert "--yes" in blocked.output

    applied = runner.invoke(
        katalog_cli.app,
        ["repair", "hierarchy", "--apply", "--yes", "--json"],
        env=environment,
    )
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.output)["applied"] is True
    assert tuple(tmp_path.glob("catalogue.sqlite3.hierarchy-repair-*.bak"))

    database = KatalogDatabase(database_path.resolve())
    try:

        def read_title(session: Session) -> str:
            item = session.get(Zaisan, malformed_id)
            assert item is not None
            return item.title

        title = database.run_transaction(read_title)
    finally:
        database.close()
    assert title == "CLI Film"


def test_collection_and_watch_order_commands_emit_stable_json(tmp_path: Path) -> None:
    runner = CliRunner()
    database_path = tmp_path / "catalogue.sqlite3"
    environment = _environment(database_path)
    _initialise(runner, environment)
    movie_id = _create_movie(database_path, tmp_path / "collection-library")

    created = runner.invoke(
        katalog_cli.app,
        ["--json", "collection", "create", "CLI collection", "--overview", "Initial"],
        env=environment,
    )
    assert created.exit_code == 0, created.output
    collection = json.loads(created.output)
    assert collection == {
        "collection_id": 1,
        "deleted": False,
        "membership": None,
        "revision": 1,
        "warnings": [],
    }
    assert (
        runner.invoke(katalog_cli.app, ["--json", "collection", "list"], env=environment).exit_code
        == 0
    )

    updated = runner.invoke(
        katalog_cli.app,
        ["--json", "collection", "update", "1", "--revision", "1", "--overview", "Updated"],
        env=environment,
    )
    assert updated.exit_code == 0, updated.output
    assert json.loads(updated.output)["revision"] == 2
    added = runner.invoke(
        katalog_cli.app,
        ["--json", "collection", "add-item", "1", str(movie_id), "--revision", "2"],
        env=environment,
    )
    assert added.exit_code == 0, added.output
    assert json.loads(added.output)["membership"]["item"]["id"] == movie_id
    shown = runner.invoke(katalog_cli.app, ["--json", "collection", "show", "1"], env=environment)
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output)["members"][0]["item"]["id"] == movie_id

    order_created = runner.invoke(
        katalog_cli.app,
        [
            "--json",
            "watch-order",
            "create",
            "1",
            "CLI order",
            "--collection-revision",
            "3",
        ],
        env=environment,
    )
    assert order_created.exit_code == 0, order_created.output
    order = json.loads(order_created.output)
    assert order["watch_order_id"] == 1
    assert (
        runner.invoke(
            katalog_cli.app, ["--json", "watch-order", "list", "1"], env=environment
        ).exit_code
        == 0
    )
    added_entry = runner.invoke(
        katalog_cli.app,
        ["--json", "watch-order", "add", "1", str(movie_id), "--revision", "1"],
        env=environment,
    )
    assert added_entry.exit_code == 0, added_entry.output
    entry_id = json.loads(added_entry.output)["entry"]["id"]
    assert (
        runner.invoke(
            katalog_cli.app,
            ["--json", "watch-order", "move", "1", str(entry_id), "--revision", "2"],
            env=environment,
        ).exit_code
        == 0
    )
    preview = runner.invoke(
        katalog_cli.app,
        ["--json", "watch-order", "preview-generation", "1", "--revision", "3", "--mode", "air"],
        env=environment,
    )
    assert preview.exit_code == 0, preview.output
    assert json.loads(preview.output)["entries"][0]["id"] == movie_id
    applied = runner.invoke(
        katalog_cli.app,
        ["--json", "watch-order", "apply-generation", "1", "--revision", "3", "--mode", "release"],
        env=environment,
    )
    assert applied.exit_code == 0, applied.output
    shown_order = runner.invoke(
        katalog_cli.app, ["--json", "watch-order", "show", "1"], env=environment
    )
    assert shown_order.exit_code == 0, shown_order.output
    assert json.loads(shown_order.output)["entries"]["items"][0]["item"]["id"] == movie_id
    removed_entry = runner.invoke(
        katalog_cli.app,
        ["--json", "watch-order", "remove", "1", str(entry_id), "--revision", "4"],
        env=environment,
    )
    assert removed_entry.exit_code == 0, removed_entry.output
    deleted_order = runner.invoke(
        katalog_cli.app,
        ["--json", "watch-order", "delete", "1", "--revision", "5", "--yes"],
        env=environment,
    )
    assert deleted_order.exit_code == 0, deleted_order.output
    removed_member = runner.invoke(
        katalog_cli.app,
        ["--json", "collection", "remove-item", "1", str(movie_id), "--revision", "5"],
        env=environment,
    )
    assert removed_member.exit_code == 0, removed_member.output
    deleted_collection = runner.invoke(
        katalog_cli.app,
        ["--json", "collection", "delete", "1", "--revision", "6", "--yes"],
        env=environment,
    )
    assert deleted_collection.exit_code == 0, deleted_collection.output


def test_item_search_prioritizes_titles_and_applies_filters(tmp_path: Path) -> None:
    runner = CliRunner()
    database_path = tmp_path / "catalogue.sqlite3"
    environment = _environment(database_path)
    _initialise(runner, environment)

    database = KatalogDatabase(database_path.resolve())
    try:

        def create(session: Session) -> None:
            movie_root = create_library_root(
                session,
                path=tmp_path / "movie-library",
                expected_media_kind=ZaisanKind.MOVIE,
            )
            series_root = create_library_root(
                session,
                path=tmp_path / "series-library",
                expected_media_kind=ZaisanKind.SERIES,
            )
            for title, year in (
                ("Cars", 2006),
                ("Cars 2", 2011),
                ("Runaway Cars", 2006),
                ("Carson", 2006),
            ):
                create_library_item(
                    session,
                    library_root_id=movie_root.id,
                    item_kind=ZaisanKind.MOVIE,
                    title=title,
                    release_year=year,
                )
            create_library_item(
                session,
                library_root_id=series_root.id,
                item_kind=ZaisanKind.SERIES,
                title="Cars Documentary",
                release_year=2006,
            )

        database.run_transaction(create)
    finally:
        database.close()

    ranked = runner.invoke(katalog_cli.app, ["--json", "item", "search", "Cars"], env=environment)
    assert ranked.exit_code == 0, ranked.output
    assert [item["title"] for item in json.loads(ranked.output)] == [
        "Cars",
        "Cars 2",
        "Cars Documentary",
        "Runaway Cars",
        "Carson",
    ]

    filtered = runner.invoke(
        katalog_cli.app,
        ["--json", "item", "search", "Cars", "--year", "2006", "--kind", "movie"],
        env=environment,
    )
    assert filtered.exit_code == 0, filtered.output
    assert [item["title"] for item in json.loads(filtered.output)] == [
        "Cars",
        "Runaway Cars",
        "Carson",
    ]


def test_scan_audit_and_status_handle_offline_roots(tmp_path: Path) -> None:
    runner = CliRunner()
    environment = _environment(tmp_path / "catalogue.sqlite3")
    missing_root = tmp_path / "not-mounted"
    _initialise(runner, environment)
    added = runner.invoke(
        katalog_cli.app,
        ["library", "add", str(missing_root), "--expected-kind", "series"],
        env=environment,
    )
    assert added.exit_code == 0, added.output

    scan = runner.invoke(
        katalog_cli.app,
        ["--json", "scan", "--root", "1"],
        env=environment,
    )
    assert scan.exit_code == 4, scan.output
    scan_result = json.loads(scan.output)
    assert scan_result["failed"] == 1
    assert scan_result["findings"] == [
        {
            "category": "unreadable_file",
            "message": "The configured library root is not an accessible directory.",
            "path": str(missing_root.resolve()),
        }
    ]

    audit = runner.invoke(
        katalog_cli.app,
        ["--json", "audit", "--root", "1", "--category", "unreadable_file"],
        env=environment,
    )
    assert audit.exit_code == 0, audit.output
    assert json.loads(audit.output)["findings"][0]["category"] == "unreadable_file"

    status = runner.invoke(katalog_cli.app, ["--json", "status"], env=environment)
    assert status.exit_code == 0, status.output
    report = json.loads(status.output)
    assert report["enabled_roots"] == 1
    assert report["unresolved_audit_issue_count"] == 1
    assert report["roots"][0]["last_scan_completed_at"] is not None


def test_cli_invalid_input_and_scan_cancellation(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    runner = CliRunner()
    environment = _environment(tmp_path / "catalogue.sqlite3")
    _initialise(runner, environment)

    invalid = runner.invoke(
        katalog_cli.app,
        ["library", "add", str(tmp_path / "media"), "--expected-kind", "documentary"],
        env=environment,
    )
    assert invalid.exit_code == 2
    assert "Invalid value" in invalid.output

    missing = runner.invoke(katalog_cli.app, ["scan", "--root", "99"], env=environment)
    assert missing.exit_code == 3
    assert "does not exist" in missing.output

    def cancel_scan(
        self: IncrementalScanner,
        *,
        root_id: int | None = None,
        include_unavailable: bool = False,
        dry_run: bool = False,
    ) -> ScanResult:
        del self, root_id, include_unavailable, dry_run
        raise KeyboardInterrupt

    monkeypatch.setattr(scanning_cli.IncrementalScanner, "scan", cancel_scan)
    cancelled = runner.invoke(katalog_cli.app, ["scan"], env=environment)
    assert cancelled.exit_code == 130
    assert cancelled.output == "Scan cancelled.\n"


def test_metadata_commands_have_json_and_confirmation_behaviour(tmp_path: Path) -> None:
    runner = CliRunner()
    database_path = tmp_path / "catalogue.sqlite3"
    environment = _environment(database_path)
    _initialise(runner, environment)
    item_id = _create_movie(database_path, tmp_path / "Movies")

    candidates = runner.invoke(
        katalog_cli.app,
        ["--json", "metadata", "candidates", str(item_id)],
        env=environment,
    )
    assert candidates.exit_code == 0, candidates.output
    assert json.loads(candidates.output) == []

    ignored = runner.invoke(
        katalog_cli.app,
        ["--json", "metadata", "ignore", str(item_id)],
        env=environment,
    )
    assert ignored.exit_code == 0, ignored.output
    assert json.loads(ignored.output)["status"] == "ignored"

    confirmation = runner.invoke(
        katalog_cli.app,
        ["metadata", "unmatch", str(item_id)],
        env=environment,
    )
    assert confirmation.exit_code == 2
    assert "--yes" in confirmation.output

    unmatched = runner.invoke(
        katalog_cli.app,
        ["--json", "metadata", "unmatch", str(item_id), "--yes"],
        env=environment,
    )
    assert unmatched.exit_code == 0, unmatched.output
    assert json.loads(unmatched.output) == {"item_id": item_id, "status": "unmatched"}

    pruned = runner.invoke(
        katalog_cli.app,
        ["--json", "artwork", "prune", "--yes"],
        env=environment,
    )
    assert pruned.exit_code == 0, pruned.output
    assert json.loads(pruned.output) == {"removed_bytes": 0, "removed_files": 0}


def test_metadata_cli_reports_invalid_filters_and_missing_provider_configuration(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.delenv("KASANA_KOURIER_TMDB_API_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    environment = _environment(tmp_path / "catalogue.sqlite3")
    _initialise(runner, environment)

    invalid = runner.invoke(
        katalog_cli.app,
        ["metadata", "review", "--min-confidence", "0.9", "--max-confidence", "0.2"],
        env=environment,
    )
    assert invalid.exit_code == 2
    assert "must not exceed" in invalid.output

    missing_provider = runner.invoke(katalog_cli.app, ["metadata", "search", "1"], env=environment)
    assert missing_provider.exit_code == 2
    assert "provider configuration" in missing_provider.output

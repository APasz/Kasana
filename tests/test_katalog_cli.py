from __future__ import annotations

import json
from pathlib import Path

from pytest import MonkeyPatch
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from kasana.katalog.cli import app as katalog_cli
from kasana.katalog.cli import scanning as scanning_cli
from kasana.katalog.database import KatalogDatabase
from kasana.katalog.models import ZaisanKind
from kasana.katalog.scanning import IncrementalScanner, ScanResult
from kasana.katalog.services import create_library_item, create_library_root


def _environment(database_path: Path) -> dict[str, str]:
    return {"KASANA_KATALOG_DATABASE_PATH": str(database_path)}


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
    current = runner.invoke(katalog_cli.app, ["--json", "database", "current"], env=environment)
    assert current.exit_code == 0, current.output
    assert json.loads(current.output) == {"revision": "20260718_0005"}

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

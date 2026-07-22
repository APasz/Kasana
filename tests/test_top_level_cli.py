"""Top-level Kasana convenience-command coverage."""

from __future__ import annotations

import json
from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch
from typer.testing import CliRunner

from kasana import cli
from kasana.kestrel.doctor import DoctorReport


def test_config_show_reports_aligned_non_secret_defaults() -> None:
    result = CliRunner().invoke(cli.app, ["config", "show", "--json"])

    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["katalog_api_url"] == "http://127.0.0.1:5373"
    assert report["kanvas_url"] == "http://127.0.0.1:5370"
    assert report["kestrel_katalog_url"] == report["katalog_api_url"]
    assert report["kourier_katalog_url"] == "http://127.0.0.1:5373/"
    assert report["log_file"] == "logs/kasana.log"

    human_result = CliRunner().invoke(cli.app, ["config", "show"])
    assert human_result.exit_code == 0, human_result.output
    assert "Kasana configuration" in human_result.output
    assert "Katalog API" in human_result.output
    assert "Log file" in human_result.output


def test_top_level_doctor_delegates_to_kestrel_readiness(monkeypatch: MonkeyPatch) -> None:
    async def fake_doctor(_settings: object) -> DoctorReport:
        return DoctorReport(
            katalog_connected=True,
            mpv_path=Path("/usr/bin/mpv"),
            mpv_version="mpv fake",
            runtime_directory_writable=True,
            temporary_directory_writable=True,
            uri_handler_registered=True,
            ipc_capable=True,
        )

    monkeypatch.setattr(cli, "_doctor", fake_doctor)
    result = CliRunner().invoke(cli.app, ["doctor", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["katalog_connected"] is True

    human_result = CliRunner().invoke(cli.app, ["doctor"])
    assert human_result.exit_code == 0, human_result.output
    assert "Playback readiness" in human_result.output
    assert "Katalog" in human_result.output

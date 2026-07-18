"""Kestrel URI handler, doctor, and command-boundary tests."""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch
from typer.testing import CliRunner

from kasana.katalog.public import (
    ManualQueuePlaybackContext,
    SeriesPlaybackContext,
    StandalonePlaybackContext,
)
from kasana.kestrel import cli
from kasana.kestrel.cli import app
from kasana.kestrel.doctor import DoctorReport, run_doctor
from kasana.kestrel.player import PlaybackOutcome, PlaybackResult
from kasana.kestrel.settings import KestrelSettings
from kasana.kestrel.uri import (
    KestrelUriError,
    install_uri_handler,
    parse_playback_uri,
    uninstall_uri_handler,
    uri_handler_is_registered,
    validate_launch_token,
)

_TOKEN = "x" * 43


@pytest.mark.parametrize(
    "uri",
    (
        "kasana:/play/" + _TOKEN,
        "kasana://play/" + _TOKEN + "?unexpected=true",
        "kasana://play/not-valid",
        "kasana://play/" + "x" * 129,
        "kasana://play:bad/" + _TOKEN,
        "https://play/" + _TOKEN,
    ),
)
def test_playback_uri_parsing_is_strict(uri: str) -> None:
    with pytest.raises(KestrelUriError):
        parse_playback_uri(uri)


def test_playback_uri_parsing_accepts_only_an_opaque_launch_token() -> None:
    assert parse_playback_uri(f"kasana://play/{_TOKEN}").launch_token == _TOKEN
    assert validate_launch_token(_TOKEN) == _TOKEN


def test_xdg_handler_is_private_to_the_user_and_uses_argument_exec(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []

    def fake_run(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(arguments)
        if arguments[1:4] == ["query", "default", "x-scheme-handler/kasana"]:
            return subprocess.CompletedProcess(arguments, 0, "kasana-kestrel.desktop\n", "")
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr("kasana.kestrel.uri.subprocess.run", fake_run)
    executable = tmp_path / "bin with spaces" / "kasana-kestrel"
    target = install_uri_handler(
        executable=executable,
        data_home=tmp_path / "xdg",
        xdg_mime_executable="fake-xdg-mime",
    )

    contents = target.read_text(encoding="utf-8")
    assert target == tmp_path / "xdg" / "applications" / "kasana-kestrel.desktop"
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    assert f'Exec="{executable}" handle-uri %u' in contents
    assert commands == [
        ["fake-xdg-mime", "default", "kasana-kestrel.desktop", "x-scheme-handler/kasana"]
    ]
    assert uri_handler_is_registered(
        data_home=tmp_path / "xdg", xdg_mime_executable="fake-xdg-mime"
    )
    assert uninstall_uri_handler(data_home=tmp_path / "xdg") == target
    assert not target.exists()


class FakeHealthClient:
    async def health(self) -> object:
        return object()

    async def close(self) -> None:
        return None


async def test_doctor_reports_fake_catalog_mpv_and_ipc_capability(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    executable = tmp_path / "mpv"
    executable.touch(mode=0o700)

    async def fake_version(path: Path) -> str | None:
        assert path == executable
        return "mpv 0.fake"

    def fake_discover(_configured: str) -> Path:
        return executable

    monkeypatch.setattr("kasana.kestrel.doctor.discover_mpv", fake_discover)
    monkeypatch.setattr("kasana.kestrel.doctor.mpv_version", fake_version)
    monkeypatch.setattr("kasana.kestrel.doctor.uri_handler_is_registered", lambda: True)
    report = await run_doctor(
        KestrelSettings(
            runtime_directory=tmp_path / "runtime",
            temporary_directory=tmp_path / "temporary",
        ),
        FakeHealthClient(),
    )

    assert report.healthy
    assert report.mpv_version == "mpv 0.fake"
    assert report.uri_handler_registered


def test_cli_play_and_handle_uri_delegate_to_the_typed_player(monkeypatch: MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_play(_settings: KestrelSettings, token: str) -> PlaybackResult:
        calls.append(token)
        return PlaybackResult(session_id="s" * 43, outcome=PlaybackOutcome.COMPLETED, returncode=0)

    monkeypatch.setattr(cli, "_play", fake_play)
    runner = CliRunner()
    direct = runner.invoke(app, ["play", _TOKEN])
    uri = runner.invoke(app, ["handle-uri", f"kasana://play/{_TOKEN}"])

    assert direct.exit_code == 0
    assert uri.exit_code == 0
    assert calls == [_TOKEN, _TOKEN]


def test_cli_rejects_a_malformed_uri_without_calling_player(monkeypatch: MonkeyPatch) -> None:
    called = False

    async def fake_play(_settings: KestrelSettings, _token: str) -> PlaybackResult:
        nonlocal called
        called = True
        raise AssertionError("invalid URI reached playback")

    monkeypatch.setattr(cli, "_play", fake_play)
    result = CliRunner().invoke(app, ["handle-uri", "kasana://play/not-valid"])

    assert result.exit_code == 2
    assert not called


def test_cli_creates_typed_playback_contexts_before_launching(monkeypatch: MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    async def fake_plan_and_play(
        _settings: KestrelSettings, user: str, playback_context: object
    ) -> PlaybackResult:
        calls.append((user, playback_context))
        return PlaybackResult(session_id="s" * 43, outcome=PlaybackOutcome.COMPLETED, returncode=0)

    monkeypatch.setattr(cli, "_plan_and_play", fake_plan_and_play)
    runner = CliRunner()

    item = runner.invoke(app, ["play-item", "42", "--user", "owner"])
    series = runner.invoke(app, ["play-series", "8", "--user", "owner", "--resume"])
    queue = runner.invoke(app, ["play-queue", "4", "9", "--user", "owner"])

    assert item.exit_code == 0, item.output
    assert series.exit_code == 0, series.output
    assert queue.exit_code == 0, queue.output
    assert isinstance(calls[0][1], StandalonePlaybackContext)
    assert isinstance(calls[1][1], SeriesPlaybackContext)
    assert isinstance(calls[2][1], ManualQueuePlaybackContext)
    assert calls[2][1].item_ids == (4, 9)


def test_cli_doctor_has_human_and_json_output(monkeypatch: MonkeyPatch) -> None:
    async def fake_doctor(_settings: KestrelSettings) -> DoctorReport:
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
    human = CliRunner().invoke(app, ["doctor"])
    assert human.exit_code == 0, human.output
    assert "Playback readiness" in human.output
    assert "mpv fake" in human.output

    machine = CliRunner().invoke(app, ["doctor", "--json"])
    assert machine.exit_code == 0, machine.output
    assert '"katalog_connected":true' in machine.output

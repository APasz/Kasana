"""Contracts for filesystem-backed Kasana configuration."""

from __future__ import annotations

import json
from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch

from kasana.kanvas.settings import Kanvas_Settings
from kasana.katalog.settings import KatalogSettings
from kasana.kestrel.settings import KestrelSettings
from kasana.kourier.settings import KourierSettings


def test_non_secret_application_preferences_load_from_configuration_file(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    configuration_directory = tmp_path / "configs"
    configuration_directory.mkdir()
    (configuration_directory / "config.katalog.json").write_text(
        json.dumps(
            {
                "api_port": 5399,
                "database_path": "catalogue.sqlite3",
            }
        ),
        encoding="utf-8",
    )
    (configuration_directory / "config.kanvas.json").write_text(
        json.dumps({"port": 5398, "accent_colour": "#123456"}), encoding="utf-8"
    )
    monkeypatch.setenv("KASANA_CONFIG_DIRECTORY", str(configuration_directory))

    katalog = KatalogSettings()
    kanvas = Kanvas_Settings()

    assert katalog.api_port == 5399
    assert katalog.database_path == Path("catalogue.sqlite3")
    assert katalog.user_configuration_directory == configuration_directory / "users"
    assert kanvas.port == 5398
    assert kanvas.accent_colour == "#123456"
    assert str(kanvas.katalog_url) == "http://127.0.0.1:5399/"
    assert KestrelSettings().katalog_url == "http://127.0.0.1:5399"
    assert str(KourierSettings().katalog_url) == "http://127.0.0.1:5399/"


def test_environment_configuration_overrides_non_secret_file_preferences(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    configuration_directory = tmp_path / "configs"
    configuration_directory.mkdir()
    (configuration_directory / "config.kanvas.json").write_text(
        json.dumps({"port": 5398}), encoding="utf-8"
    )
    monkeypatch.setenv("KASANA_CONFIG_DIRECTORY", str(configuration_directory))
    monkeypatch.setenv("KASANA_KANVAS_PORT", "5499")

    assert Kanvas_Settings().port == 5499

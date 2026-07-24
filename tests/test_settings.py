import logging
from collections.abc import Callable
from pathlib import Path
from typing import cast

from _pytest.monkeypatch import MonkeyPatch

from kasana.kanvas.settings import Kanvas_Settings
from kasana.katalog.settings import KatalogSettings
from kasana.kestrel.settings import KestrelSettings, PlayerBackend
from kasana.kourier.settings import KourierSettings, TMDBSettings
from kasana.shared.logging import LogDomain, LogLevel, configure_logging
from kasana.shared.settings import SharedSettings


def test_component_settings_use_distinct_environment_prefixes(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("KASANA_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("KASANA_KATALOG_API_PORT", "9123")
    monkeypatch.setenv("KASANA_KESTREL_PLAYER_BACKEND", "vlc")

    assert SharedSettings().log_level is LogLevel.DEBUG
    assert SharedSettings().log_directory == Path("logs")
    assert KatalogSettings().api_port == 9123
    assert KestrelSettings().player_backend is PlayerBackend.VLC
    assert Kanvas_Settings().host == "0.0.0.0"
    assert Kanvas_Settings().port == 5370
    assert Kanvas_Settings().auto_browser_open is False
    assert KestrelSettings().katalog_url == "http://127.0.0.1:9123"
    assert str(KourierSettings().katalog_url) == "http://127.0.0.1:9123/"


def test_kanvas_auto_browser_open_reads_typed_environment(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("KASANA_KANVAS_AUTO_BROWSER_OPEN", "true")

    assert Kanvas_Settings().auto_browser_open is True


def test_shared_logging_writes_to_configured_file(tmp_path: Path) -> None:
    log_directory = tmp_path / "logs"
    previous_log = log_directory / "katalog.log"
    previous_log.parent.mkdir()
    previous_log.write_text("previous session", encoding="utf-8")
    other_domain_log = log_directory / "kanvas.log"
    other_domain_log.write_text("still running", encoding="utf-8")
    previous_archive = tmp_path / "logs.old"
    previous_archive.mkdir()
    (previous_archive / "katalog.log").write_text("obsolete", encoding="utf-8")

    configure_logging(LogLevel.INFO, LogDomain.KATALOG, log_directory)
    logging.getLogger("kasana.tests").info("file logging works")
    for handler in logging.getLogger().handlers:
        handler.flush()

    assert "file logging works" in (log_directory / "katalog.log").read_text(encoding="utf-8")
    assert (previous_archive / "katalog.log").read_text(encoding="utf-8") == "previous session"
    assert other_domain_log.read_text(encoding="utf-8") == "still running"


def test_tmdb_settings_read_typed_provider_environment(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("KASANA_KOURIER_TMDB_API_TOKEN", "test-token")
    monkeypatch.setenv("KASANA_KOURIER_TMDB_CONCURRENCY", "6")

    settings = cast(Callable[[], TMDBSettings], TMDBSettings)()

    assert settings.api_token.get_secret_value() == "test-token"
    assert settings.concurrency == 6

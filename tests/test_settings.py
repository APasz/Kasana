from collections.abc import Callable
from typing import cast

from _pytest.monkeypatch import MonkeyPatch

from kasana.kanvas.settings import Kanvas_Settings
from kasana.katalog.settings import KatalogSettings
from kasana.kestrel.settings import KestrelSettings, PlayerBackend
from kasana.kourier.settings import KourierSettings, TMDBSettings
from kasana.shared.logging import LogLevel
from kasana.shared.settings import SharedSettings


def test_component_settings_use_distinct_environment_prefixes(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("KASANA_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("KASANA_KATALOG_API_PORT", "9123")
    monkeypatch.setenv("KASANA_KESTREL_PLAYER_BACKEND", "vlc")

    assert SharedSettings().log_level is LogLevel.DEBUG
    assert KatalogSettings().api_port == 9123
    assert KestrelSettings().player_backend is PlayerBackend.VLC
    assert Kanvas_Settings().port == 5370
    assert Kanvas_Settings().auto_browser_open is False
    assert KestrelSettings().katalog_url == "http://127.0.0.1:5373"
    assert str(KourierSettings().katalog_url) == "http://127.0.0.1:5373/"


def test_kanvas_auto_browser_open_reads_typed_environment(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("KASANA_KANVAS_AUTO_BROWSER_OPEN", "true")

    assert Kanvas_Settings().auto_browser_open is True


def test_tmdb_settings_read_typed_provider_environment(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("KASANA_KOURIER_TMDB_API_TOKEN", "test-token")
    monkeypatch.setenv("KASANA_KOURIER_TMDB_CONCURRENCY", "6")

    settings = cast(Callable[[], TMDBSettings], TMDBSettings)()

    assert settings.api_token.get_secret_value() == "test-token"
    assert settings.concurrency == 6

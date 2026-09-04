import pytest
from _pytest.monkeypatch import MonkeyPatch
from fastapi import FastAPI

from kasana.__main__ import main as kasana_main
from kasana.kanvas import dashboard
from kasana.kanvas.__main__ import main as kanvas_main
from kasana.kanvas.dashboard import build_dashboard
from kasana.kanvas.routes import runtime as route_runtime
from kasana.kanvas.settings import Kanvas_Settings
from kasana.katalog.api import server as katalog_server
from kasana.katalog.backend import create_backend
from kasana.katalog.cli.app import main as katalog_main
from kasana.katalog.settings import KatalogSettings
from kasana.kestrel.__main__ import main as kestrel_main
from kasana.kourier.__main__ import main as kourier_main


def test_katalog_backend_can_be_constructed() -> None:
    assert isinstance(create_backend(KatalogSettings()), FastAPI)


def test_component_entry_points_configure_without_starting_services() -> None:
    kasana_main()
    katalog_main()
    kanvas_main()
    kestrel_main()
    kourier_main()


def test_katalog_api_server_uses_the_shared_graceful_shutdown_timeout(
    monkeypatch: MonkeyPatch,
) -> None:
    captured_timeout: list[int | None] = []

    def fake_run(server: katalog_server.uvicorn.Server) -> None:
        captured_timeout.append(server.config.timeout_graceful_shutdown)

    monkeypatch.setattr(katalog_server.uvicorn.Server, "run", fake_run)

    katalog_server.main()

    assert captured_timeout == [5]


def test_katalog_api_server_suppresses_normal_keyboard_interrupt(
    monkeypatch: MonkeyPatch,
) -> None:
    def fake_run(_server: katalog_server.uvicorn.Server) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(katalog_server.uvicorn.Server, "run", fake_run)

    katalog_server.main()


def test_dashboard_can_be_composed() -> None:
    build_dashboard()


def test_dashboard_rejects_reconfiguration_after_assets_are_registered(
    monkeypatch: MonkeyPatch,
) -> None:
    first_settings = Kanvas_Settings()
    monkeypatch.setattr(dashboard, "_assets_registered", True)
    monkeypatch.setattr(route_runtime.runtime, "settings", first_settings)

    with pytest.raises(RuntimeError, match="already configured with different settings"):
        dashboard.build_dashboard(Kanvas_Settings(port=5371))

from fastapi import FastAPI

from kasana.kanvas.__main__ import main as kanvas_main
from kasana.kanvas.dashboard import build_dashboard
from kasana.katalog.backend import create_backend
from kasana.katalog.cli.app import main as katalog_main
from kasana.katalog.settings import KatalogSettings
from kasana.kestrel.__main__ import main as kestrel_main
from kasana.kourier.__main__ import main as kourier_main


def test_katalog_backend_can_be_constructed() -> None:
    assert isinstance(create_backend(KatalogSettings()), FastAPI)


def test_component_entry_points_configure_without_starting_services() -> None:
    katalog_main()
    kanvas_main()
    kestrel_main()
    kourier_main()


def test_dashboard_can_be_composed() -> None:
    build_dashboard()

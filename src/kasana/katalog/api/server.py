"""Console entry point for the Katalog FastAPI server."""

from __future__ import annotations

import uvicorn

from kasana.katalog.api.app import create_app
from kasana.katalog.settings import KatalogSettings
from kasana.shared import SharedSettings, configure_logging


def main() -> None:
    settings = KatalogSettings()
    shared_settings = SharedSettings()
    configure_logging(shared_settings.log_level, shared_settings.log_file)
    uvicorn.run(
        create_app(settings),
        host=settings.api_host,
        port=settings.api_port,
        log_config=None,
    )

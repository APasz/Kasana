"""Console entry point for the Katalog FastAPI server."""

from __future__ import annotations

import uvicorn

from kasana.katalog.api.app import create_app
from kasana.katalog.settings import KatalogSettings
from kasana.shared import LogDomain, SharedSettings, configure_logging


def main() -> None:
    settings = KatalogSettings()
    shared_settings = SharedSettings()
    config = uvicorn.Config(
        create_app(settings),
        host=settings.api_host,
        port=settings.api_port,
        log_config=None,
        timeout_graceful_shutdown=shared_settings.graceful_shutdown_timeout_seconds,
    )
    configure_logging(shared_settings.log_level, LogDomain.KATALOG, shared_settings.log_directory)
    try:
        uvicorn.Server(config).run()
    except KeyboardInterrupt:
        return

"""Console entry point for the Katalog FastAPI server."""

from __future__ import annotations

import uvicorn

from kasana.katalog.api.app import create_app
from kasana.katalog.settings import KatalogSettings


def main() -> None:
    settings = KatalogSettings()
    uvicorn.run(create_app(settings), host=settings.api_host, port=settings.api_port)

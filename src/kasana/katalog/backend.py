"""Backward-free application construction for the Katalog HTTP server."""

from fastapi import FastAPI

from kasana.katalog.api.app import create_app
from kasana.katalog.settings import KatalogSettings


def create_backend(settings: KatalogSettings) -> FastAPI:
    """Create the Katalog FastAPI application without starting a server."""

    return create_app(settings)

"""Application-scoped HTTP sessions for Kanvas-to-Katalog requests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Protocol

import aiohttp

from kasana.kanvas.settings import Kanvas_Settings
from kasana.katalog.public import KatalogClient


class KatalogClientFactory(Protocol):
    """Construct a client over an optional shared aiohttp session."""

    def __call__(
        self,
        base_url: str,
        *,
        timeout_seconds: float,
        session: aiohttp.ClientSession | None = None,
    ) -> KatalogClient: ...


class _KatalogClientPoolClosedError(RuntimeError):
    """Signal a request that raced Kanvas application shutdown."""


class KatalogClientPool:
    """Own a reusable Katalog HTTP session for the lifetime of a Kanvas app."""

    def __init__(self, settings: Kanvas_Settings) -> None:
        self._base_url = str(settings.katalog_url)
        self._timeout_seconds = settings.katalog_timeout_seconds
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()
        self._closed = False

    def matches(self, settings: Kanvas_Settings) -> bool:
        return (
            not self._closed
            and self._base_url == str(settings.katalog_url)
            and self._timeout_seconds == settings.katalog_timeout_seconds
        )

    async def session(self) -> aiohttp.ClientSession:
        async with self._lock:
            if self._closed:
                raise _KatalogClientPoolClosedError("Katalog client pool is closed.")
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self._timeout_seconds)
                )
            return self._session

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            session = self._session
            self._session = None
        if session is not None and not session.closed:
            close_task = asyncio.create_task(session.close())
            try:
                await asyncio.shield(close_task)
            except asyncio.CancelledError:
                await asyncio.shield(close_task)
                raise


_client_pool: KatalogClientPool | None = None


async def start_katalog_client_pool(settings: Kanvas_Settings) -> None:
    """Configure the app-level pool before routes begin serving requests."""

    global _client_pool
    existing_pool = _client_pool
    if existing_pool is not None and existing_pool.matches(settings):
        return
    if existing_pool is not None:
        await existing_pool.close()
    _client_pool = KatalogClientPool(settings)


async def close_katalog_client_pool() -> None:
    """Close pooled keep-alive connections during NiceGUI shutdown."""

    global _client_pool
    pool = _client_pool
    _client_pool = None
    if pool is not None:
        await pool.close()


async def create_katalog_client(
    settings: Kanvas_Settings,
    *,
    client_factory: KatalogClientFactory = KatalogClient,
) -> KatalogClient:
    """Create a client that borrows the app pool when the app is running."""

    pool = _client_pool
    if pool is None or not pool.matches(settings):
        return client_factory(
            str(settings.katalog_url), timeout_seconds=settings.katalog_timeout_seconds
        )
    try:
        session = await pool.session()
    except _KatalogClientPoolClosedError:
        return client_factory(
            str(settings.katalog_url), timeout_seconds=settings.katalog_timeout_seconds
        )
    return client_factory(
        str(settings.katalog_url),
        timeout_seconds=settings.katalog_timeout_seconds,
        session=session,
    )


@asynccontextmanager
async def katalog_client_context(
    settings: Kanvas_Settings,
    *,
    client_factory: KatalogClientFactory = KatalogClient,
) -> AsyncGenerator[KatalogClient]:
    """Yield a Katalog client and close only any one-shot fallback session."""

    client = await create_katalog_client(settings, client_factory=client_factory)
    async with client:
        yield client

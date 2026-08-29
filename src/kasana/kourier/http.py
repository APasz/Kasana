"""Shared bounded HTTP helpers for Kourier provider integrations."""

from __future__ import annotations

import asyncio
from asyncio.locks import Lock as AsyncLock
from asyncio.locks import Semaphore
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from math import isfinite
from pathlib import Path
from threading import Lock as ThreadLock
from time import monotonic
from typing import Protocol, cast

import aiohttp
from yarl import URL

from kasana.kourier.errors import KourierError
from kasana.shared.concurrency import run_blocking
from kasana.shared.metadata import ArtworkDownload, ProviderCapability, ProviderErrorCategory

type AsyncSleeper = Callable[[float], Awaitable[None]]
type Clock = Callable[[], datetime]
type MonotonicClock = Callable[[], float]

KASANA_USER_AGENT = "Kasana/0.1 (+https://github.com/APasz/Kasana)"


class RetrySettings(Protocol):
    """The retry-related settings required by a provider HTTP client."""

    max_retries: int
    retry_backoff_seconds: float
    max_backoff_seconds: float


class HttpSettings(RetrySettings, Protocol):
    """The transport settings required by a provider HTTP client."""

    timeout_seconds: float
    concurrency: int
    requests_per_second: float


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True)
class ArtworkDownloadResponse:
    status: int
    headers: Mapping[str, str]
    content_type: str | None
    size_bytes: int


def _request_interval(requests_per_second: float) -> float:
    if not isfinite(requests_per_second) or requests_per_second <= 0:
        raise ValueError("Requests per second must be a finite positive number.")
    interval_seconds = 1 / requests_per_second
    if not isfinite(interval_seconds):
        raise ValueError("Requests per second must be a finite positive number.")
    return interval_seconds


class RequestPacer:
    """Serialise starts and provider-wide cooldowns at a bounded request rate."""

    def __init__(
        self,
        requests_per_second: float,
        *,
        sleeper: AsyncSleeper = asyncio.sleep,
        clock: MonotonicClock = monotonic,
    ) -> None:
        self._interval_seconds = _request_interval(requests_per_second)
        self._sleeper = sleeper
        self._clock = clock
        self._lock = ThreadLock()
        self._last_request_at: float | None = None
        self._next_request_at = 0.0

    async def wait(self) -> None:
        """Wait until this request may start, including any shared cooldown."""

        while True:
            with self._lock:
                now = self._clock()
                delay = self._next_request_at - now
                if delay <= 0:
                    self._last_request_at = now
                    self._next_request_at = now + self._interval_seconds
                    return
            await self._sleeper(delay)

    async def defer(self, delay_seconds: float) -> None:
        """Prevent new requests until a provider's rate-limit delay has elapsed."""

        if not isfinite(delay_seconds):
            raise ValueError("Request delay must be finite.")
        if delay_seconds <= 0:
            return
        with self._lock:
            self._next_request_at = max(self._next_request_at, self._clock() + delay_seconds)

    def restrict_to(self, requests_per_second: float) -> None:
        """Retain the strictest configured rate for a shared provider controller."""

        interval_seconds = _request_interval(requests_per_second)
        with self._lock:
            if interval_seconds <= self._interval_seconds:
                return
            self._interval_seconds = interval_seconds
            if self._last_request_at is not None:
                self._next_request_at = max(
                    self._next_request_at, self._last_request_at + interval_seconds
                )


_SHARED_REQUEST_PACERS: dict[str, RequestPacer] = {}
_SHARED_REQUEST_PACERS_LOCK = ThreadLock()


def shared_request_pacer(provider_name: str, requests_per_second: float) -> RequestPacer:
    """Return the process-shared pace controller for a provider."""

    with _SHARED_REQUEST_PACERS_LOCK:
        pacer = _SHARED_REQUEST_PACERS.get(provider_name)
        if pacer is None:
            pacer = RequestPacer(requests_per_second)
            _SHARED_REQUEST_PACERS[provider_name] = pacer
        else:
            pacer.restrict_to(requests_per_second)
        return pacer


class RetryPolicy:
    """Bounded exponential retry policy shared by JSON and artwork requests."""

    def __init__(self, settings: RetrySettings, sleeper: AsyncSleeper, clock: Clock) -> None:
        self.settings = settings
        self.sleeper = sleeper
        self.clock = clock

    async def connection_error(self, attempt: int) -> bool:
        if attempt >= self.settings.max_retries:
            return False
        await self.sleeper(self.backoff_delay(attempt))
        return True

    async def status(self, attempt: int, headers: Mapping[str, str]) -> bool:
        delay = self.status_delay(attempt, headers)
        if delay is None:
            return False
        await self.sleeper(delay)
        return True

    def status_delay(self, attempt: int, headers: Mapping[str, str]) -> float | None:
        """Return the required retry delay, or ``None`` when attempts are exhausted."""

        if attempt >= self.settings.max_retries:
            return None
        return self.response_delay(attempt, headers)

    def response_delay(self, attempt: int, headers: Mapping[str, str]) -> float:
        """Return the server or bounded-backoff delay for an unsuccessful response."""

        retry_after = retry_after_seconds(headers, self.clock)
        return retry_after if retry_after is not None else self.backoff_delay(attempt)

    def backoff_delay(self, attempt: int) -> float:
        return min(
            self.settings.max_backoff_seconds,
            self.settings.retry_backoff_seconds * (2**attempt),
        )


class BoundedHttpProvider:
    """Shared session, retry, and artwork transport for provider adapters."""

    def __init__(
        self,
        settings: HttpSettings,
        *,
        provider_name: str,
        display_name: str,
        session: aiohttp.ClientSession | None,
        sleeper: AsyncSleeper,
        clock: Clock | None,
        request_pacer: RequestPacer | None = None,
    ) -> None:
        self._provider_name = provider_name
        self._display_name = display_name
        self._session = session
        self._owns_session = session is None
        self._session_lock = AsyncLock()
        self._semaphore = Semaphore(settings.concurrency)
        self._timeout = aiohttp.ClientTimeout(total=settings.timeout_seconds)
        self._retry = RetryPolicy(settings, sleeper, clock or (lambda: datetime.now(UTC)))
        self._requests_per_second = settings.requests_per_second
        self._request_pacer = request_pacer

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        raise NotImplementedError

    def supports(self, capability: ProviderCapability) -> bool:
        return capability in self.capabilities

    @property
    def session(self) -> aiohttp.ClientSession | None:
        return self._session

    async def close(self) -> None:
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()

    async def _fetch_json(
        self,
        url: URL,
        headers: Mapping[str, str],
        parameters: Mapping[str, str] | None = None,
    ) -> Mapping[str, object]:
        for attempt in range(self._retry.settings.max_retries + 1):
            try:
                response = await self._request(url, headers, parameters)
            except TimeoutError as error:
                raise KourierError(
                    ProviderErrorCategory.TIMEOUT,
                    f"{self._display_name} request timed out.",
                    provider=self.provider_name,
                ) from error
            except aiohttp.ClientConnectionError as error:
                if await self._retry.connection_error(attempt):
                    continue
                raise KourierError(
                    ProviderErrorCategory.TRANSIENT,
                    f"{self._display_name} connection failed after retries.",
                    provider=self.provider_name,
                ) from error
            except aiohttp.ClientError as error:
                raise KourierError(
                    ProviderErrorCategory.REQUEST_FAILED,
                    f"{self._display_name} request failed.",
                    provider=self.provider_name,
                ) from error
            if 200 <= response.status < 300:
                return decode_json(
                    response.body,
                    provider=self.provider_name,
                    display_name=self._display_name,
                )
            if await self._retryable_status(attempt, response.status, response.headers):
                continue
            raise self._response_error(response.status)
        raise RuntimeError(f"{self._display_name} retry loop ended unexpectedly.")

    async def _fetch_artwork(self, url: URL) -> tuple[bytes, str | None]:
        for attempt in range(self._retry.settings.max_retries + 1):
            try:
                response = await self._request_artwork(url)
            except TimeoutError as error:
                raise KourierError(
                    ProviderErrorCategory.TIMEOUT,
                    f"{self._display_name} artwork request timed out.",
                    provider=self.provider_name,
                ) from error
            except aiohttp.ClientConnectionError as error:
                if await self._retry.connection_error(attempt):
                    continue
                raise KourierError(
                    ProviderErrorCategory.TRANSIENT,
                    f"{self._display_name} artwork connection failed after retries.",
                    provider=self.provider_name,
                ) from error
            except aiohttp.ClientError as error:
                raise KourierError(
                    ProviderErrorCategory.REQUEST_FAILED,
                    f"{self._display_name} artwork request failed.",
                    provider=self.provider_name,
                ) from error
            if 200 <= response.status < 300:
                return response.body, _header_value(response.headers, "Content-Type")
            if await self._retryable_status(attempt, response.status, response.headers):
                continue
            raise self._response_error(response.status)
        raise RuntimeError(f"{self._display_name} artwork retry loop ended unexpectedly.")

    async def _download_artwork(
        self, url: URL, destination: Path, maximum_size_bytes: int
    ) -> ArtworkDownload:
        if maximum_size_bytes < 1:
            raise ValueError("Artwork maximum size must be positive.")
        for attempt in range(self._retry.settings.max_retries + 1):
            try:
                response = await self._stream_artwork(url, destination, maximum_size_bytes)
            except TimeoutError as error:
                raise KourierError(
                    ProviderErrorCategory.TIMEOUT,
                    f"{self._display_name} artwork request timed out.",
                    provider=self.provider_name,
                ) from error
            except aiohttp.ClientConnectionError as error:
                if await self._retry.connection_error(attempt):
                    continue
                raise KourierError(
                    ProviderErrorCategory.TRANSIENT,
                    f"{self._display_name} artwork connection failed after retries.",
                    provider=self.provider_name,
                ) from error
            except aiohttp.ClientError as error:
                raise KourierError(
                    ProviderErrorCategory.REQUEST_FAILED,
                    f"{self._display_name} artwork request failed.",
                    provider=self.provider_name,
                ) from error
            if 200 <= response.status < 300:
                return ArtworkDownload(
                    content_type=response.content_type, size_bytes=response.size_bytes
                )
            if await self._retryable_status(attempt, response.status, response.headers):
                continue
            raise self._response_error(response.status)
        raise RuntimeError(f"{self._display_name} artwork retry loop ended unexpectedly.")

    async def _retryable_status(
        self, attempt: int, status: int, headers: Mapping[str, str]
    ) -> bool:
        if status == 429 or status >= 500:
            if status == 429:
                await self._pacer().defer(self._retry.response_delay(attempt, headers))
            return await self._retry.status(attempt, headers)
        return False

    def _pacer(self) -> RequestPacer:
        if self._request_pacer is None:
            self._request_pacer = shared_request_pacer(
                self.provider_name, self._requests_per_second
            )
        return self._request_pacer

    def _response_error(self, status: int) -> KourierError:
        if status == 429:
            category = ProviderErrorCategory.RATE_LIMITED
        elif status >= 500:
            category = ProviderErrorCategory.TRANSIENT
        elif status in {401, 403}:
            category = ProviderErrorCategory.AUTHENTICATION
        elif status == 404:
            category = ProviderErrorCategory.NOT_FOUND
        else:
            category = ProviderErrorCategory.REQUEST_FAILED
        return http_error(self.provider_name, self._display_name, category, status)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is not None:
            return self._session
        async with self._session_lock:
            if self._session is None:
                self._session = aiohttp.ClientSession(timeout=self._timeout)
            return self._session

    async def _request(
        self, url: URL, headers: Mapping[str, str], parameters: Mapping[str, str] | None
    ) -> Response:
        session = await self._get_session()
        async with self._semaphore:
            await self._pacer().wait()
            if parameters is None:
                async with session.get(url, headers=headers, timeout=self._timeout) as reply:
                    return Response(reply.status, dict(reply.headers), await reply.read())
            async with session.get(
                url, params=parameters, headers=headers, timeout=self._timeout
            ) as reply:
                return Response(reply.status, dict(reply.headers), await reply.read())

    async def _request_artwork(self, url: URL) -> Response:
        session = await self._get_session()
        headers = {"User-Agent": KASANA_USER_AGENT, "Accept": "image/*"}
        async with self._semaphore:
            await self._pacer().wait()
            async with session.get(url, headers=headers, timeout=self._timeout) as reply:
                return Response(reply.status, dict(reply.headers), await reply.read())

    async def _stream_artwork(
        self, url: URL, destination: Path, maximum_size_bytes: int
    ) -> ArtworkDownloadResponse:
        session = await self._get_session()
        headers = {"User-Agent": KASANA_USER_AGENT, "Accept": "image/*"}
        async with self._semaphore:
            await self._pacer().wait()
            async with session.get(url, headers=headers, timeout=self._timeout) as reply:
                response_headers = dict(reply.headers)
                if not 200 <= reply.status < 300:
                    return ArtworkDownloadResponse(reply.status, response_headers, None, 0)
                await run_blocking(truncate_file, destination)
                size_bytes = 0
                async for chunk in reply.content.iter_chunked(64 * 1024):
                    size_bytes += len(chunk)
                    if size_bytes > maximum_size_bytes:
                        raise KourierError(
                            ProviderErrorCategory.REQUEST_FAILED,
                            f"Artwork response exceeds {maximum_size_bytes} bytes.",
                            provider=self.provider_name,
                        )
                    await run_blocking(append_file, destination, chunk)
                return ArtworkDownloadResponse(
                    reply.status,
                    response_headers,
                    _header_value(response_headers, "Content-Type"),
                    size_bytes,
                )


def _header_value(headers: Mapping[str, str], name: str) -> str | None:
    value = headers.get(name)
    if value is not None:
        return value
    folded_name = name.casefold()
    return next(
        (
            header_value
            for header_name, header_value in headers.items()
            if header_name.casefold() == folded_name
        ),
        None,
    )


def retry_after_seconds(headers: Mapping[str, str], clock: Clock) -> float | None:
    value = _header_value(headers, "Retry-After")
    if value is None:
        return None
    value = value.strip()
    if value.isascii() and value.isdecimal():
        try:
            delay_seconds = float(value)
        except ValueError:
            return None
        return delay_seconds if isfinite(delay_seconds) else None
    try:
        retry_time = parsedate_to_datetime(value)
    except TypeError, ValueError:
        return None
    if retry_time.tzinfo is None:
        retry_time = retry_time.replace(tzinfo=UTC)
    now = clock()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return max(0.0, (retry_time - now).total_seconds())


def decode_json(body: bytes, *, provider: str, display_name: str) -> Mapping[str, object]:
    """Decode an object JSON response into the provider-neutral HTTP shape."""

    import json

    try:
        decoded: object = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise KourierError(
            ProviderErrorCategory.MALFORMED_RESPONSE,
            f"{display_name} returned invalid JSON.",
            provider=provider,
        ) from error
    if not isinstance(decoded, dict):
        raise KourierError(
            ProviderErrorCategory.MALFORMED_RESPONSE,
            f"{display_name} returned a non-object JSON response.",
            provider=provider,
        )
    payload = cast(dict[object, object], decoded)
    if not all(isinstance(key, str) for key in payload):
        raise KourierError(
            ProviderErrorCategory.MALFORMED_RESPONSE,
            f"{display_name} returned a JSON object with non-string keys.",
            provider=provider,
        )
    return cast(dict[str, object], payload)


def http_error(
    provider: str, display_name: str, category: ProviderErrorCategory, status_code: int
) -> KourierError:
    return KourierError(
        category,
        f"{display_name} returned HTTP {status_code}.",
        provider=provider,
        status_code=status_code,
    )


def request_error(provider: str, message: str) -> KourierError:
    return KourierError(ProviderErrorCategory.REQUEST_FAILED, message, provider=provider)


def truncate_file(path: Path) -> None:
    path.write_bytes(b"")


def append_file(path: Path, content: bytes) -> None:
    with path.open("ab") as file:
        file.write(content)

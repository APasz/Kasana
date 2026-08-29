"""Shared bounded HTTP helpers for Kourier provider integrations."""

from __future__ import annotations

from asyncio.locks import Lock, Semaphore
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Protocol, cast

import aiohttp
from yarl import URL

from kasana.kourier.errors import KourierError
from kasana.shared.concurrency import run_blocking
from kasana.shared.metadata import ArtworkDownload, ProviderCapability, ProviderErrorCategory

type AsyncSleeper = Callable[[float], Awaitable[None]]
type Clock = Callable[[], datetime]

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
        if attempt >= self.settings.max_retries:
            return False
        retry_after = retry_after_seconds(headers, self.clock)
        await self.sleeper(retry_after if retry_after is not None else self.backoff_delay(attempt))
        return True

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
    ) -> None:
        self._provider_name = provider_name
        self._display_name = display_name
        self._session = session
        self._owns_session = session is None
        self._session_lock = Lock()
        self._semaphore = Semaphore(settings.concurrency)
        self._timeout = aiohttp.ClientTimeout(total=settings.timeout_seconds)
        self._retry = RetryPolicy(settings, sleeper, clock or (lambda: datetime.now(UTC)))

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
                return response.body, response.headers.get("Content-Type")
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
            return await self._retry.status(attempt, headers)
        return False

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
            async with session.get(url, headers=headers, timeout=self._timeout) as reply:
                return Response(reply.status, dict(reply.headers), await reply.read())

    async def _stream_artwork(
        self, url: URL, destination: Path, maximum_size_bytes: int
    ) -> ArtworkDownloadResponse:
        session = await self._get_session()
        headers = {"User-Agent": KASANA_USER_AGENT, "Accept": "image/*"}
        async with self._semaphore:
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
                    response_headers.get("Content-Type"),
                    size_bytes,
                )


def retry_after_seconds(headers: Mapping[str, str], clock: Clock) -> float | None:
    value = headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
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

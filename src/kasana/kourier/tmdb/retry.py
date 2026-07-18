"""Retry timing and HTTP response classification for TMDB."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from kasana.kourier.errors import KourierError
from kasana.kourier.settings import TMDBSettings
from kasana.shared.metadata import ProviderErrorCategory

type AsyncSleeper = Callable[[float], Awaitable[None]]
type Clock = Callable[[], datetime]

TMDB_PROVIDER = "tmdb"


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

    def __init__(self, settings: TMDBSettings, sleeper: AsyncSleeper, clock: Clock) -> None:
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


def http_error(category: ProviderErrorCategory, status_code: int) -> KourierError:
    return KourierError(
        category,
        f"TMDB returned HTTP {status_code}.",
        provider=TMDB_PROVIDER,
        status_code=status_code,
    )


def request_error(message: str) -> KourierError:
    return KourierError(ProviderErrorCategory.REQUEST_FAILED, message, provider=TMDB_PROVIDER)

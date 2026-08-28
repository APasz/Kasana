"""HTTP file-transfer policy for token-authorised Katalog media files.

The policy owns HTTP range semantics, leaving token validation and future delivery
mechanisms (such as nginx ``X-Accel-Redirect``) independent from API contracts.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from datetime import UTC
from email.utils import format_datetime, parsedate_to_datetime
from typing import BinaryIO, Protocol
from urllib.parse import quote

from fastapi import Response, status
from starlette.types import Receive, Scope, Send

from kasana.katalog.api.service import MediaTransferFile
from kasana.shared.concurrency import BlockingExecutor, run_blocking


class FileTransferPolicy(Protocol):
    """Build a file response after Katalog has authorised one media file."""

    def response(
        self,
        media_file: MediaTransferFile,
        *,
        method: str,
        range_header: str | None,
        if_none_match: str | None,
        download: bool,
        if_range: str | None = None,
    ) -> Response: ...


@dataclass(frozen=True)
class RangeStreamingFileTransferPolicy:
    """Bounded, cancellation-safe local-file streaming with one byte range."""

    chunk_size: int
    blocking_executor: BlockingExecutor | None = None

    def __post_init__(self) -> None:
        if self.chunk_size <= 0:
            msg = "The media transfer chunk size must be positive."
            raise ValueError(msg)

    def response(
        self,
        media_file: MediaTransferFile,
        *,
        method: str,
        range_header: str | None,
        if_none_match: str | None,
        download: bool,
        if_range: str | None = None,
    ) -> Response:
        headers = _base_headers(media_file, download=download)
        if if_none_match is not None and _etag_matches(if_none_match, media_file.etag):
            return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
        selected_range = _parse_range(
            _range_header_for_if_range(range_header, if_range, media_file),
            media_file.size_bytes,
        )
        if selected_range is None:
            start = 0
            end = media_file.size_bytes - 1
            status_code = status.HTTP_200_OK
        elif isinstance(selected_range, _InvalidRange):
            headers["Content-Range"] = f"bytes */{media_file.size_bytes}"
            return Response(
                status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
                headers=headers,
            )
        else:
            start = selected_range.start
            end = selected_range.end
            headers["Content-Range"] = f"bytes {start}-{end}/{media_file.size_bytes}"
            status_code = status.HTTP_206_PARTIAL_CONTENT
        length = end - start + 1 if media_file.size_bytes else 0
        headers["Content-Length"] = str(length)
        if method == "HEAD":
            return Response(
                status_code=status_code, headers=headers, media_type=media_file.content_type
            )
        return BoundedFileResponse(
            media_file,
            start=start,
            length=length,
            chunk_size=self.chunk_size,
            status_code=status_code,
            headers=headers,
            blocking_executor=self.blocking_executor,
        )


@dataclass(frozen=True)
class _ByteRange:
    start: int
    end: int


@dataclass(frozen=True)
class _InvalidRange:
    pass


_INVALID_RANGE = _InvalidRange()


class BoundedFileResponse(Response):
    """A response that owns a bounded file iterator without buffering its body."""

    def __init__(
        self,
        media_file: MediaTransferFile,
        *,
        start: int,
        length: int,
        chunk_size: int,
        status_code: int,
        headers: dict[str, str],
        blocking_executor: BlockingExecutor | None,
    ) -> None:
        super().__init__(
            content=None,
            status_code=status_code,
            headers=headers,
            media_type=media_file.content_type,
        )
        self._media_file = media_file
        self._start = start
        self._length = length
        self._chunk_size = chunk_size
        self._blocking_executor = blocking_executor

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await send(
            {"type": "http.response.start", "status": self.status_code, "headers": self.raw_headers}
        )
        chunks = _file_chunks(
            self._media_file,
            start=self._start,
            length=self._length,
            chunk_size=self._chunk_size,
            blocking_executor=self._blocking_executor,
        )
        try:
            async for chunk in chunks:
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
        finally:
            await chunks.aclose()
        await send({"type": "http.response.body", "body": b"", "more_body": False})
        if self.background is not None:
            await self.background()


def _base_headers(media_file: MediaTransferFile, *, download: bool) -> dict[str, str]:
    headers = {
        "Accept-Ranges": "bytes",
        "ETag": media_file.etag,
        "Last-Modified": format_datetime(media_file.last_modified, usegmt=True),
    }
    if download:
        headers["Content-Disposition"] = (
            f"attachment; filename*=UTF-8''{quote(media_file.download_name)}"
        )
    return headers


def _etag_matches(if_none_match: str, etag: str) -> bool:
    for candidate in if_none_match.split(","):
        normalised = candidate.strip()
        if normalised == "*":
            return True
        if normalised.startswith("W/"):
            normalised = normalised[2:].strip()
        if normalised == etag:
            return True
    return False


def _range_header_for_if_range(
    range_header: str | None, if_range: str | None, media_file: MediaTransferFile
) -> str | None:
    """Use a requested byte range only when its validator still describes this file."""

    if range_header is None or if_range is None:
        return range_header
    return range_header if _if_range_matches(if_range, media_file) else None


def _if_range_matches(if_range: str, media_file: MediaTransferFile) -> bool:
    candidate = if_range.strip()
    if candidate == media_file.etag:
        return True
    if candidate.startswith("W/"):
        return False
    try:
        date_value = parsedate_to_datetime(candidate)
    except (IndexError, OverflowError, TypeError, ValueError):
        return False
    if date_value.tzinfo is None:
        return False
    last_modified = media_file.last_modified.astimezone(UTC).replace(microsecond=0)
    return last_modified <= date_value.astimezone(UTC)


def _parse_range(range_header: str | None, size_bytes: int) -> _ByteRange | _InvalidRange | None:
    if range_header is None:
        return None
    unit, separator, specification = range_header.partition("=")
    if unit.strip().casefold() != "bytes" or separator != "=" or "," in specification:
        return _INVALID_RANGE
    start_text, dash, end_text = specification.strip().partition("-")
    if dash != "-" or (not start_text and not end_text):
        return _INVALID_RANGE
    if start_text:
        if not start_text.isdecimal():
            return _INVALID_RANGE
        start = int(start_text)
        if start >= size_bytes:
            return _INVALID_RANGE
        if end_text:
            if not end_text.isdecimal():
                return _INVALID_RANGE
            end = min(int(end_text), size_bytes - 1)
            if end < start:
                return _INVALID_RANGE
        else:
            end = size_bytes - 1
        return _ByteRange(start=start, end=end)
    if not end_text.isdecimal():
        return _INVALID_RANGE
    suffix_length = int(end_text)
    if suffix_length <= 0 or size_bytes == 0:
        return _INVALID_RANGE
    start = max(size_bytes - suffix_length, 0)
    return _ByteRange(start=start, end=size_bytes - 1)


async def _file_chunks(
    media_file: MediaTransferFile,
    *,
    start: int,
    length: int,
    chunk_size: int,
    blocking_executor: BlockingExecutor | None,
) -> AsyncGenerator[bytes]:
    file_handle = await _run_file_operation(blocking_executor, _open_file, media_file)
    try:
        await _run_file_operation(blocking_executor, _seek_file, file_handle, start)
        remaining = length
        while remaining > 0:
            chunk = await _run_file_operation(
                blocking_executor, _read_chunk, file_handle, min(chunk_size, remaining)
            )
            if not chunk:
                return
            remaining -= len(chunk)
            yield chunk
    finally:
        close_task = asyncio.create_task(
            _run_file_operation(blocking_executor, _close_file, file_handle)
        )
        try:
            await asyncio.shield(close_task)
        except asyncio.CancelledError:
            await asyncio.shield(close_task)
            raise


def _read_chunk(file_handle: BinaryIO, size: int) -> bytes:
    return file_handle.read(size)


def _open_file(media_file: MediaTransferFile) -> BinaryIO:
    return media_file.path.open("rb")


def _seek_file(file_handle: BinaryIO, start: int) -> None:
    file_handle.seek(start)


def _close_file(file_handle: BinaryIO) -> None:
    file_handle.close()


async def _run_file_operation[Result](
    blocking_executor: BlockingExecutor | None,
    operation: Callable[..., Result],
    /,
    *args: object,
) -> Result:
    """Use the transfer-specific pool when Katalog configured one."""

    if blocking_executor is None:
        return await run_blocking(operation, *args)
    return await blocking_executor.run(operation, *args)

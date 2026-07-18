"""Katalog-owned artwork caching and lifecycle management."""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pydantic import AnyHttpUrl, BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from kasana.katalog.database import KatalogDatabase
from kasana.katalog.metadata.refresh import ArtworkStreamingProvider, MetadataProvider, provider_for
from kasana.katalog.models import (
    CachedArtwork,
    CachedArtworkKind,
    MetadataCandidate,
    MetadataCandidateStatus,
    Zaisan,
)
from kasana.shared.concurrency import run_blocking
from kasana.shared.metadata import (
    ArtworkContent,
    ArtworkDownload,
    ArtworkKind,
    ArtworkReference,
    ProviderCapability,
)

_IMAGE_SIGNATURES: dict[str, bytes] = {
    "image/jpeg": b"\xff\xd8\xff",
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/gif": b"GIF8",
}
_IMAGE_SUFFIXES: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


class ArtworkCacheView(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    library_item_id: int | None
    provider: str
    provider_id: str
    kind: CachedArtworkKind
    cache_path: Path
    size_bytes: int
    content_type: str


@dataclass(frozen=True)
class ArtworkRequest:
    library_item_id: int
    provider: str
    provider_id: str
    source_url: str
    revision: str


class ArtworkCache:
    """Stores provider artwork atomically, with database records as the source of truth."""

    def __init__(
        self,
        database: KatalogDatabase,
        cache_path: Path,
        *,
        concurrency: int,
        maximum_size_bytes: int,
    ) -> None:
        if concurrency < 1 or maximum_size_bytes < 1:
            msg = "Artwork concurrency and artwork maximum size must be positive."
            raise ValueError(msg)
        self.database = database
        self.cache_path = cache_path.expanduser().resolve(strict=False)
        self.concurrency = concurrency
        self.maximum_size_bytes = maximum_size_bytes

    async def fetch_posters(
        self, providers: tuple[MetadataProvider, ...], *, root_id: int | None
    ) -> tuple[ArtworkCacheView, ...]:
        requests = await run_blocking(self._poster_requests, root_id)
        semaphore = asyncio.Semaphore(self.concurrency)

        async def fetch(request: ArtworkRequest) -> ArtworkCacheView | None:
            async with semaphore:
                provider = provider_for(request.provider, providers)
                return await self._cache_artwork(provider, request)

        results = await asyncio.gather(*(fetch(request) for request in requests))
        return tuple(result for result in results if result is not None)

    async def prune(self) -> tuple[int, int]:
        records = await run_blocking(self._unreferenced_artwork)
        removed_files = 0
        removed_bytes = 0
        for record in records:
            path = self.cache_path / record.cache_relative_path
            size = await run_blocking(unlink_if_exists, path)
            removed_files += 1
            removed_bytes += size
            await run_blocking(self._delete_artwork_record, record.id)
        return removed_files, removed_bytes

    def _poster_requests(self, root_id: int | None) -> tuple[ArtworkRequest, ...]:
        def load(session: Session) -> tuple[ArtworkRequest, ...]:
            statement = (
                select(MetadataCandidate)
                .join(Zaisan)
                .where(
                    MetadataCandidate.status == MetadataCandidateStatus.ACCEPTED,
                    MetadataCandidate.poster_source_url.is_not(None),
                    MetadataCandidate.poster_revision.is_not(None),
                )
            )
            if root_id is not None:
                statement = statement.where(Zaisan.library_root_id == root_id)
            requests: dict[tuple[str, str, str], ArtworkRequest] = {}
            for candidate in session.scalars(statement).all():
                key = (candidate.provider, candidate.provider_id, candidate.poster_revision or "")
                requests.setdefault(
                    key,
                    ArtworkRequest(
                        library_item_id=candidate.library_item_id,
                        provider=candidate.provider,
                        provider_id=candidate.provider_id,
                        source_url=candidate.poster_source_url or "",
                        revision=candidate.poster_revision or "",
                    ),
                )
            return tuple(requests.values())

        return self.database.run_transaction(load)

    async def _cache_artwork(
        self, provider: MetadataProvider, request: ArtworkRequest
    ) -> ArtworkCacheView | None:
        existing = await run_blocking(
            self._cached_artwork, request.provider, request.provider_id, request.revision
        )
        if existing is not None:
            return existing
        if not provider.supports(ProviderCapability.GET_ARTWORK):
            return None
        reference = ArtworkReference(
            provider=request.provider,
            kind=ArtworkKind.POSTER,
            raw_path=request.revision,
            source_url=AnyHttpUrl(request.source_url),
        )
        relative_path = artwork_relative_path(
            request.provider,
            request.provider_id,
            CachedArtworkKind.POSTER,
            request.revision,
            "image/jpeg",
        )
        destination = self.cache_path / relative_path
        temporary_path = await run_blocking(create_artwork_temporary_path, destination)
        moved = False
        try:
            content_type, size_bytes = await self._download_artwork(
                provider, reference, temporary_path
            )
            final_relative_path = artwork_relative_path(
                request.provider,
                request.provider_id,
                CachedArtworkKind.POSTER,
                request.revision,
                content_type,
            )
            destination = self.cache_path / final_relative_path
            await run_blocking(move_file_atomically, temporary_path, destination)
            moved = True
            return await run_blocking(
                self._persist_artwork,
                request,
                final_relative_path,
                content_type,
                size_bytes,
            )
        except BaseException:
            await run_blocking(unlink_if_exists, temporary_path)
            if moved:
                await run_blocking(unlink_if_exists, destination)
            raise

    async def _download_artwork(
        self, provider: MetadataProvider, reference: ArtworkReference, temporary_path: Path
    ) -> tuple[str, int]:
        if hasattr(provider, "download_artwork"):
            streaming_provider = cast(ArtworkStreamingProvider, provider)
            download: ArtworkDownload = await streaming_provider.download_artwork(
                reference,
                temporary_path,
                maximum_size_bytes=self.maximum_size_bytes,
            )
            content_type = validated_image_type(
                download.content_type, await run_blocking(read_artwork_signature, temporary_path)
            )
            return content_type, download.size_bytes
        content: ArtworkContent = await provider.get_artwork(reference)
        await run_blocking(
            write_artwork_content, temporary_path, content.content, self.maximum_size_bytes
        )
        content_type = validated_image_type(
            content.media_type, await run_blocking(read_artwork_signature, temporary_path)
        )
        return content_type, len(content.content)

    def _cached_artwork(
        self, provider: str, provider_id: str, revision: str
    ) -> ArtworkCacheView | None:
        def load(session: Session) -> ArtworkCacheView | None:
            record = session.scalar(
                select(CachedArtwork).where(
                    CachedArtwork.provider == provider,
                    CachedArtwork.provider_id == provider_id,
                    CachedArtwork.artwork_kind == CachedArtworkKind.POSTER,
                    CachedArtwork.provider_revision == revision,
                )
            )
            if record is None:
                return None
            path = self.cache_path / record.cache_relative_path
            return artwork_view(record, self.cache_path) if path.is_file() else None

        return self.database.run_transaction(load)

    def _persist_artwork(
        self,
        request: ArtworkRequest,
        relative_path: Path,
        content_type: str,
        size_bytes: int,
    ) -> ArtworkCacheView:
        def persist(session: Session) -> ArtworkCacheView:
            record = session.scalar(
                select(CachedArtwork).where(
                    CachedArtwork.provider == request.provider,
                    CachedArtwork.provider_id == request.provider_id,
                    CachedArtwork.artwork_kind == CachedArtworkKind.POSTER,
                    CachedArtwork.provider_revision == request.revision,
                )
            )
            if record is None:
                record = CachedArtwork(
                    library_item_id=request.library_item_id,
                    provider=request.provider,
                    provider_id=request.provider_id,
                    artwork_kind=CachedArtworkKind.POSTER,
                    provider_revision=request.revision,
                    source_url=request.source_url,
                    attribution=request.provider,
                    content_type=content_type,
                    cache_relative_path=str(relative_path),
                    size_bytes=size_bytes,
                    downloaded_at=datetime.now(UTC),
                )
                session.add(record)
            session.flush()
            return artwork_view(record, self.cache_path)

        return self.database.run_transaction(persist)

    def _unreferenced_artwork(self) -> tuple[CachedArtwork, ...]:
        def load(session: Session) -> tuple[CachedArtwork, ...]:
            records = session.scalars(select(CachedArtwork)).all()
            referenced = {
                (candidate.provider, candidate.provider_id, candidate.poster_revision)
                for candidate in session.scalars(
                    select(MetadataCandidate).where(
                        MetadataCandidate.status == MetadataCandidateStatus.ACCEPTED,
                        MetadataCandidate.poster_revision.is_not(None),
                    )
                ).all()
            }
            unreferenced = [
                record
                for record in records
                if (record.provider, record.provider_id, record.provider_revision) not in referenced
            ]
            for record in unreferenced:
                session.expunge(record)
            return tuple(unreferenced)

        return self.database.run_transaction(load)

    def _delete_artwork_record(self, record_id: int) -> None:
        def delete(session: Session) -> None:
            record = session.get(CachedArtwork, record_id)
            if record is not None:
                session.delete(record)

        self.database.run_transaction(delete)


def artwork_view(record: CachedArtwork, cache_path: Path) -> ArtworkCacheView:
    return ArtworkCacheView(
        id=record.id,
        library_item_id=record.library_item_id,
        provider=record.provider,
        provider_id=record.provider_id,
        kind=record.artwork_kind,
        cache_path=cache_path / record.cache_relative_path,
        size_bytes=record.size_bytes,
        content_type=record.content_type,
    )


def validated_image_type(content_type: str | None, content: bytes) -> str:
    normalized = (content_type or "").split(";", 1)[0].strip().casefold()
    if normalized in _IMAGE_SIGNATURES and content.startswith(_IMAGE_SIGNATURES[normalized]):
        return normalized
    for image_type, signature in _IMAGE_SIGNATURES.items():
        if content.startswith(signature):
            return image_type
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    msg = "Artwork response is not a supported image type."
    raise ValueError(msg)


def artwork_relative_path(
    provider: str,
    provider_id: str,
    kind: CachedArtworkKind,
    revision: str,
    content_type: str,
) -> Path:
    suffix = _IMAGE_SUFFIXES.get(content_type)
    if suffix is None:
        msg = f"Unsupported artwork content type {content_type!r}."
        raise ValueError(msg)
    digest = hashlib.sha256(
        f"{provider}\0{provider_id}\0{kind.value}\0{revision}".encode()
    ).hexdigest()
    return Path(provider) / kind.value / f"{digest}{suffix}"


def create_artwork_temporary_path(destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    os.close(descriptor)
    return Path(temporary_name)


def write_artwork_content(temporary_path: Path, content: bytes, maximum_size: int) -> None:
    if len(content) > maximum_size:
        msg = f"Artwork response exceeds {maximum_size} bytes."
        raise ValueError(msg)
    temporary_path.write_bytes(content)


def read_artwork_signature(path: Path) -> bytes:
    with path.open("rb") as file:
        return file.read(16)


def move_file_atomically(temporary_path: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path.replace(destination)


def unlink_if_exists(path: Path) -> int:
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return 0
    path.unlink()
    return size

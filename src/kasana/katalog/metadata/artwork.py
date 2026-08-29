"""Katalog-owned artwork caching and lifecycle management."""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from kasana.katalog.database import KatalogDatabase
from kasana.katalog.limits import MAX_ARTWORK_PER_ITEM
from kasana.katalog.metadata.refresh import (
    ArtworkStreamingProvider,
    ExternalPosterArtworkProvider,
    MetadataProvider,
    PosterArtworkProvider,
    provider_for,
)
from kasana.katalog.models import (
    CachedArtwork,
    CachedArtworkKind,
    MetadataBinding,
    MetadataCandidate,
    MetadataCandidateStatus,
    MetadataMatchStatus,
    Zaisan,
)
from kasana.shared.concurrency import run_blocking
from kasana.shared.metadata import (
    ArtworkContent,
    ArtworkDownload,
    ArtworkKind,
    ArtworkReference,
    ExternalIdentifier,
    PosterListing,
    PosterLookup,
    ProviderCapability,
    ProviderMediaKind,
    ProviderReference,
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
    is_primary: bool


@dataclass(frozen=True)
class ArtworkRequest:
    library_item_id: int
    provider: str
    provider_id: str
    owner_provider: str
    owner_provider_id: str
    media_kind: ProviderMediaKind
    source_url: str
    revision: str
    external_ids: tuple[ExternalIdentifier, ...] = ()
    language: str | None = None
    width: int | None = None
    height: int | None = None
    vote_average: float | None = None
    vote_count: int | None = None
    is_primary: bool = False
    display_order: int = 0
    has_variant_metadata: bool = False


@dataclass(frozen=True)
class PosterVariantBatch:
    """One provider response plus whether it authoritatively listed all variants."""

    library_item_id: int
    provider: str
    provider_id: str
    requests: tuple[ArtworkRequest, ...]
    variants_listed: bool


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
        self,
        providers: tuple[MetadataProvider, ...],
        *,
        root_id: int | None = None,
        item_id: int | None = None,
        include_variants: bool = False,
    ) -> tuple[ArtworkCacheView, ...]:
        """Cache accepted posters, with variants only for an explicit item picker."""

        if root_id is not None and item_id is not None:
            msg = "Artwork fetch accepts either a library root or an item, not both."
            raise ValueError(msg)
        primary_requests = await run_blocking(self._poster_requests, root_id, item_id)
        batches: tuple[PosterVariantBatch, ...] = ()
        if include_variants:
            batch_groups = await asyncio.gather(
                *(self._poster_variant_batches(providers, request) for request in primary_requests)
            )
            batches = tuple(batch for group in batch_groups for batch in group)
            requests = tuple(request for batch in batches for request in batch.requests)
        else:
            requests = primary_requests
        semaphore = asyncio.Semaphore(self.concurrency)

        async def fetch(request: ArtworkRequest) -> ArtworkCacheView | None:
            async with semaphore:
                provider = provider_for(request.provider, providers)
                return await self._cache_artwork(provider, request)

        results = await asyncio.gather(*(fetch(request) for request in requests))
        if include_variants and all(result is not None for result in results):
            await asyncio.gather(
                *(
                    self._remove_stale_poster_variants(batch)
                    for batch in batches
                    if batch.variants_listed
                )
            )
        return tuple(result for result in results if result is not None)

    async def prune(self) -> tuple[int, int]:
        records = await run_blocking(self._unreferenced_artwork)
        return await self._remove_artwork_records(records)

    async def _remove_artwork_records(self, records: tuple[CachedArtwork, ...]) -> tuple[int, int]:
        removed_files = 0
        removed_bytes = 0
        for record in records:
            path = self.cache_path / record.cache_relative_path
            size = await run_blocking(unlink_if_exists, path)
            removed_files += 1
            removed_bytes += size
            await run_blocking(self._delete_artwork_record, record.id)
        return removed_files, removed_bytes

    async def _remove_stale_poster_variants(self, batch: PosterVariantBatch) -> None:
        """Drop superseded poster choices while preserving the library's saved choice."""

        records = await run_blocking(self._stale_poster_variants, batch)
        await self._remove_artwork_records(records)

    def _poster_requests(
        self, root_id: int | None, item_id: int | None
    ) -> tuple[ArtworkRequest, ...]:
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
            if item_id is not None:
                statement = statement.where(Zaisan.id == item_id)
            candidates = tuple(
                session.scalars(
                    statement.order_by(
                        MetadataCandidate.library_item_id,
                        MetadataCandidate.provider,
                        MetadataCandidate.provider_id,
                    )
                ).all()
            )
            if not candidates:
                return ()
            item_ids = tuple({candidate.library_item_id for candidate in candidates})
            bindings = {
                binding.library_item_id: binding
                for binding in session.scalars(
                    select(MetadataBinding).where(
                        MetadataBinding.library_item_id.in_(item_ids),
                        MetadataBinding.status == MetadataMatchStatus.MATCHED,
                    )
                ).all()
            }
            requests: dict[tuple[str, str, str], ArtworkRequest] = {}
            for candidate in candidates:
                key = (candidate.provider, candidate.provider_id, candidate.poster_revision or "")
                requests.setdefault(
                    key,
                    ArtworkRequest(
                        library_item_id=candidate.library_item_id,
                        provider=candidate.provider,
                        provider_id=candidate.provider_id,
                        owner_provider=candidate.provider,
                        owner_provider_id=candidate.provider_id,
                        media_kind=ProviderMediaKind(candidate.provider_media_kind.value),
                        source_url=candidate.poster_source_url or "",
                        revision=candidate.poster_revision or "",
                        external_ids=external_identifiers(
                            candidate, bindings.get(candidate.library_item_id)
                        ),
                        is_primary=True,
                    ),
                )
            return tuple(requests.values())

        return self.database.run_transaction(load)

    async def _poster_variant_batches(
        self, providers: tuple[MetadataProvider, ...], primary: ArtworkRequest
    ) -> tuple[PosterVariantBatch, ...]:
        """Load primary and supplemental poster sources for one matched item."""

        primary_provider = provider_for(primary.provider, providers)
        primary_batch = await self._primary_poster_variant_batch(primary_provider, primary)
        supplemental_batches = await asyncio.gather(
            *(
                self._supplemental_poster_variant_batch(provider, primary)
                for provider in providers
                if provider.provider_name != primary.provider
                and provider.supports(ProviderCapability.LIST_POSTERS_BY_EXTERNAL_ID)
            )
        )
        return self._select_poster_batches(
            (primary_batch, *(batch for batch in supplemental_batches if batch is not None))
        )

    async def _primary_poster_variant_batch(
        self, provider: MetadataProvider, primary: ArtworkRequest
    ) -> PosterVariantBatch:
        """Combine the matched poster with optional provider variants in display order."""

        primary_reference = ArtworkReference(
            provider=primary.provider,
            kind=ArtworkKind.POSTER,
            raw_path=primary.revision,
            source_url=AnyHttpUrl(primary.source_url),
            is_primary=True,
        )
        variants: tuple[ArtworkReference, ...] = ()
        variants_listed = provider.supports(ProviderCapability.LIST_POSTERS)
        if variants_listed:
            if not hasattr(provider, "list_posters"):
                msg = f"Provider {primary.provider!r} advertises unsupported poster variants."
                raise ValueError(msg)
            poster_provider = cast(PosterArtworkProvider, provider)
            variants = await poster_provider.list_posters(
                ProviderReference(provider=primary.provider, raw_id=primary.provider_id),
                primary.media_kind,
            )

        unique: dict[str, ArtworkReference] = {primary_reference.raw_path: primary_reference}
        variant_revisions: set[str] = set()
        for variant in variants:
            if variant.provider != primary.provider or variant.kind is not ArtworkKind.POSTER:
                msg = f"Provider {primary.provider!r} returned an invalid poster variant."
                raise ValueError(msg)
            if variant.source_url is None:
                msg = f"Provider {primary.provider!r} returned a poster without a source URL."
                raise ValueError(msg)
            if variant.raw_path == primary_reference.raw_path:
                unique[variant.raw_path] = variant.model_copy(update={"is_primary": True})
            else:
                unique.setdefault(
                    variant.raw_path, variant.model_copy(update={"is_primary": False})
                )
            variant_revisions.add(variant.raw_path)

        return PosterVariantBatch(
            library_item_id=primary.library_item_id,
            provider=primary.provider,
            provider_id=primary.provider_id,
            requests=tuple(
                artwork_request(
                    primary,
                    reference,
                    provider=primary.provider,
                    provider_id=primary.provider_id,
                    has_variant_metadata=reference.raw_path in variant_revisions,
                )
                for reference in unique.values()
            ),
            variants_listed=variants_listed,
        )

    async def _supplemental_poster_variant_batch(
        self, provider: MetadataProvider, primary: ArtworkRequest
    ) -> PosterVariantBatch | None:
        if not hasattr(provider, "list_posters_by_external_id"):
            msg = (
                f"Provider {provider.provider_name!r} advertises unsupported supplemental posters."
            )
            raise ValueError(msg)
        poster_provider = cast(ExternalPosterArtworkProvider, provider)
        listing = await poster_provider.list_posters_by_external_id(
            PosterLookup(
                reference=ProviderReference(provider=primary.provider, raw_id=primary.provider_id),
                media_kind=primary.media_kind,
                external_ids=primary.external_ids,
            )
        )
        if listing is None:
            return None
        self._validate_poster_listing(provider, listing)
        unique: dict[str, ArtworkReference] = {}
        for reference in listing.posters:
            unique.setdefault(
                reference.raw_path, reference.model_copy(update={"is_primary": False})
            )
        return PosterVariantBatch(
            library_item_id=primary.library_item_id,
            provider=listing.provider,
            provider_id=listing.provider_id,
            requests=tuple(
                artwork_request(
                    primary,
                    reference,
                    provider=listing.provider,
                    provider_id=listing.provider_id,
                    has_variant_metadata=True,
                )
                for reference in unique.values()
            ),
            variants_listed=True,
        )

    def _select_poster_batches(
        self, batches: tuple[PosterVariantBatch, ...]
    ) -> tuple[PosterVariantBatch, ...]:
        """Keep the matched poster first, then fairly interleave each artwork source."""

        primary_batch, *supplemental_batches = batches
        if not primary_batch.requests:
            msg = "A matched poster source returned no primary poster."
            raise ValueError(msg)
        selected: list[list[ArtworkRequest]] = [[] for _ in batches]
        selected[0].append(replace(primary_batch.requests[0], display_order=0))
        queues = [list(primary_batch.requests[1:])]
        queues.extend(list(batch.requests) for batch in supplemental_batches)
        selected_count = 1
        while selected_count < MAX_ARTWORK_PER_ITEM:
            found_request = False
            for index, queue in enumerate(queues):
                if not queue:
                    continue
                selected[index].append(replace(queue.pop(0), display_order=selected_count))
                selected_count += 1
                found_request = True
                if selected_count == MAX_ARTWORK_PER_ITEM:
                    break
            if not found_request:
                break
        return tuple(
            replace(batch, requests=tuple(requests))
            for batch, requests in zip(batches, selected, strict=True)
        )

    @staticmethod
    def _validate_poster_listing(provider: MetadataProvider, listing: PosterListing) -> None:
        if listing.provider != provider.provider_name:
            msg = (
                f"Provider {provider.provider_name!r} returned a listing for {listing.provider!r}."
            )
            raise ValueError(msg)
        for reference in listing.posters:
            if reference.provider != listing.provider or reference.kind is not ArtworkKind.POSTER:
                msg = f"Provider {listing.provider!r} returned an invalid poster variant."
                raise ValueError(msg)
            if reference.source_url is None:
                msg = f"Provider {listing.provider!r} returned a poster without a source URL."
                raise ValueError(msg)

    async def _cache_artwork(
        self, provider: MetadataProvider, request: ArtworkRequest
    ) -> ArtworkCacheView | None:
        existing = await run_blocking(
            self._cached_artwork, request.provider, request.provider_id, request.revision
        )
        if existing is not None:
            return await run_blocking(self._update_cached_artwork, existing.id, request)
        if not provider.supports(ProviderCapability.GET_ARTWORK):
            return None
        reference = ArtworkReference(
            provider=request.provider,
            kind=ArtworkKind.POSTER,
            raw_path=request.revision,
            source_url=AnyHttpUrl(request.source_url),
            language=request.language,
            width=request.width,
            height=request.height,
            vote_average=request.vote_average,
            vote_count=request.vote_count,
            is_primary=request.is_primary,
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

    def _update_cached_artwork(self, artwork_id: int, request: ArtworkRequest) -> ArtworkCacheView:
        def update(session: Session) -> ArtworkCacheView:
            record = session.get(CachedArtwork, artwork_id)
            if record is None:
                msg = f"Cached artwork {artwork_id} no longer exists."
                raise RuntimeError(msg)
            apply_artwork_request(record, request)
            session.flush()
            return artwork_view(record, self.cache_path)

        return self.database.run_transaction(update)

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
                    owner_provider=request.owner_provider,
                    owner_provider_id=request.owner_provider_id,
                    artwork_kind=CachedArtworkKind.POSTER,
                    provider_revision=request.revision,
                    source_url=request.source_url,
                    attribution=request.provider,
                    language=request.language,
                    width=request.width,
                    height=request.height,
                    vote_average=request.vote_average,
                    vote_count=request.vote_count,
                    is_primary=request.is_primary,
                    display_order=request.display_order,
                    content_type=content_type,
                    cache_relative_path=str(relative_path),
                    size_bytes=size_bytes,
                    downloaded_at=datetime.now(UTC),
                )
                session.add(record)
            else:
                apply_artwork_request(
                    record,
                    request,
                    relative_path=relative_path,
                    content_type=content_type,
                    size_bytes=size_bytes,
                    downloaded_at=datetime.now(UTC),
                )
            session.flush()
            return artwork_view(record, self.cache_path)

        return self.database.run_transaction(persist)

    def _unreferenced_artwork(self) -> tuple[CachedArtwork, ...]:
        def load(session: Session) -> tuple[CachedArtwork, ...]:
            records = session.scalars(select(CachedArtwork)).all()
            referenced = {
                (candidate.provider, candidate.provider_id)
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
                if (
                    record.owner_provider or record.provider,
                    record.owner_provider_id or record.provider_id,
                )
                not in referenced
            ]
            for record in unreferenced:
                session.expunge(record)
            return tuple(unreferenced)

        return self.database.run_transaction(load)

    def _stale_poster_variants(self, batch: PosterVariantBatch) -> tuple[CachedArtwork, ...]:
        revisions = {entry.revision for entry in batch.requests}

        def load(session: Session) -> tuple[CachedArtwork, ...]:
            item = session.get(Zaisan, batch.library_item_id)
            if item is None:
                return ()
            selected_id = item.selected_artwork_ids.get(ArtworkKind.POSTER.value)
            statement = select(CachedArtwork).where(
                CachedArtwork.library_item_id == batch.library_item_id,
                CachedArtwork.provider == batch.provider,
                CachedArtwork.provider_id == batch.provider_id,
                CachedArtwork.artwork_kind == CachedArtworkKind.POSTER,
            )
            if revisions:
                statement = statement.where(CachedArtwork.provider_revision.not_in(revisions))
            records = session.scalars(statement).all()
            stale = tuple(record for record in records if record.id != selected_id)
            for record in stale:
                session.expunge(record)
            return stale

        return self.database.run_transaction(load)

    def _delete_artwork_record(self, record_id: int) -> None:
        def delete(session: Session) -> None:
            record = session.get(CachedArtwork, record_id)
            if record is not None:
                session.delete(record)

        self.database.run_transaction(delete)


def external_identifiers(
    candidate: MetadataCandidate, binding: MetadataBinding | None
) -> tuple[ExternalIdentifier, ...]:
    """Combine the candidate's identity with validated IDs from its active binding."""

    identifiers: dict[tuple[str, str], ExternalIdentifier] = {}

    def add(identifier: ExternalIdentifier) -> None:
        identifiers.setdefault((identifier.namespace, identifier.value), identifier)

    add(ExternalIdentifier(namespace=candidate.provider, value=candidate.provider_id))
    if binding is not None:
        for raw_identifier in binding.provider_external_ids:
            try:
                add(ExternalIdentifier.model_validate(raw_identifier))
            except ValidationError as error:
                msg = f"Metadata binding {binding.id} has an invalid external identifier."
                raise ValueError(msg) from error
    return tuple(identifiers.values())


def artwork_request(
    primary: ArtworkRequest,
    reference: ArtworkReference,
    *,
    provider: str,
    provider_id: str,
    has_variant_metadata: bool,
) -> ArtworkRequest:
    """Map one validated provider reference to Katalog's cache request shape."""

    if reference.source_url is None:
        msg = f"Provider {provider!r} returned a poster without a source URL."
        raise ValueError(msg)
    return ArtworkRequest(
        library_item_id=primary.library_item_id,
        provider=provider,
        provider_id=provider_id,
        owner_provider=primary.owner_provider,
        owner_provider_id=primary.owner_provider_id,
        media_kind=primary.media_kind,
        source_url=str(reference.source_url),
        revision=reference.raw_path,
        language=reference.language,
        width=reference.width,
        height=reference.height,
        vote_average=reference.vote_average,
        vote_count=reference.vote_count,
        is_primary=reference.is_primary,
        has_variant_metadata=has_variant_metadata,
    )


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
        is_primary=record.is_primary,
    )


def apply_artwork_request(
    record: CachedArtwork,
    request: ArtworkRequest,
    *,
    relative_path: Path | None = None,
    content_type: str | None = None,
    size_bytes: int | None = None,
    downloaded_at: datetime | None = None,
) -> None:
    """Refresh cached metadata without discarding details absent from a primary fetch."""

    record.source_url = request.source_url
    record.attribution = request.provider
    record.owner_provider = request.owner_provider
    record.owner_provider_id = request.owner_provider_id
    if request.has_variant_metadata or request.language is not None:
        record.language = request.language
    if request.has_variant_metadata or request.width is not None:
        record.width = request.width
    if request.has_variant_metadata or request.height is not None:
        record.height = request.height
    if request.has_variant_metadata or request.vote_average is not None:
        record.vote_average = request.vote_average
    if request.has_variant_metadata or request.vote_count is not None:
        record.vote_count = request.vote_count
    record.is_primary = request.is_primary
    record.display_order = request.display_order
    if relative_path is not None:
        record.cache_relative_path = str(relative_path)
    if content_type is not None:
        record.content_type = content_type
    if size_bytes is not None:
        record.size_bytes = size_bytes
    if downloaded_at is not None:
        record.downloaded_at = downloaded_at


def validated_image_type(content_type: str | None, content: bytes) -> str:
    normalised: str = (content_type or "").split(";", 1)[0].strip().casefold()
    if normalised in _IMAGE_SIGNATURES and content.startswith(_IMAGE_SIGNATURES[normalised]):
        return normalised
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

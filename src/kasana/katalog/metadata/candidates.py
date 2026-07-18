"""Candidate persistence, queries, and reviewable views."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql.selectable import Select

from kasana.katalog.database import KatalogDatabase
from kasana.katalog.metadata.review import (
    MetadataWorkflowError,
    require_item,
    review_event,
    score_explanation,
)
from kasana.katalog.metadata.scoring import ScoredSearchResult, library_kind
from kasana.katalog.models import (
    MetadataCandidate,
    MetadataCandidateStatus,
    MetadataReviewAction,
    Zaisan,
    ZaisanKind,
)
from kasana.shared.metadata import SearchResult


class CandidateView(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    library_item_id: int
    item_title: str
    item_release_year: int | None
    provider: str
    provider_id: str
    media_kind: ZaisanKind
    title: str
    original_title: str | None
    release_year: int | None
    confidence: float
    explanation: tuple[dict[str, str | float], ...]
    status: MetadataCandidateStatus


@dataclass(frozen=True)
class PersistedCandidates:
    candidates: tuple[CandidateView, ...]
    rejected_keys: frozenset[tuple[str, str]]


def persist_candidates(
    database: KatalogDatabase, item_id: int, scored: Sequence[ScoredSearchResult]
) -> PersistedCandidates:
    """Upsert provider discoveries, retaining rejections until data changes."""

    def persist(session: Session) -> PersistedCandidates:
        require_item(session, item_id)
        now = datetime.now(UTC)
        existing = {
            (candidate.provider, candidate.provider_id): candidate
            for candidate in session.scalars(
                select(MetadataCandidate).where(MetadataCandidate.library_item_id == item_id)
            ).all()
        }
        changed: list[MetadataCandidate] = []
        rejected_keys: set[tuple[str, str]] = set()
        for score in scored:
            result = score.result
            key = (result.reference.provider, result.reference.raw_id)
            candidate = existing.get(key)
            provider_year = result.release_date.year if result.release_date is not None else None
            if candidate is not None and candidate.status is MetadataCandidateStatus.REJECTED:
                if candidate_is_unchanged(candidate, result, provider_year):
                    candidate.last_seen_at = now
                    rejected_keys.add(key)
                    continue
            if candidate is None:
                candidate = MetadataCandidate(
                    library_item_id=item_id,
                    provider=result.reference.provider,
                    provider_id=result.reference.raw_id,
                    provider_media_kind=library_kind(result.media_kind),
                    provider_title=result.title,
                    last_seen_at=now,
                    confidence=score.confidence,
                    scoring_explanation=score_explanation(score.explanation),
                    status=MetadataCandidateStatus.SUGGESTED,
                )
                session.add(candidate)
            candidate.provider_media_kind = library_kind(result.media_kind)
            candidate.provider_title = result.title
            candidate.provider_original_title = result.original_title
            candidate.provider_release_year = provider_year
            candidate.provider_original_language = result.original_language
            candidate.poster_source_url = (
                str(result.poster.source_url)
                if result.poster and result.poster.source_url
                else None
            )
            candidate.poster_revision = (
                result.poster.raw_path if result.poster is not None else None
            )
            candidate.confidence = score.confidence
            candidate.scoring_explanation = score_explanation(score.explanation)
            candidate.status = MetadataCandidateStatus.SUGGESTED
            candidate.last_seen_at = now
            candidate.rejected_at = None
            changed.append(candidate)
        session.flush()
        for candidate in changed:
            review_event(
                session,
                item_id,
                MetadataReviewAction.SUGGESTED,
                "automatic",
                candidate=candidate,
                details=candidate.scoring_explanation,
            )
        return PersistedCandidates(
            candidates=tuple(candidate_view(candidate) for candidate in changed),
            rejected_keys=frozenset(rejected_keys),
        )

    return database.run_transaction(persist)


def reject_candidate(
    database: KatalogDatabase, item_id: int, provider: str, provider_id: str, actor: str
) -> None:
    def reject(session: Session) -> None:
        candidate = session.scalar(
            select(MetadataCandidate).where(
                MetadataCandidate.library_item_id == item_id,
                MetadataCandidate.provider == provider,
                MetadataCandidate.provider_id == provider_id,
            )
        )
        if candidate is None:
            msg = f"Candidate {provider}:{provider_id} does not exist for item {item_id}."
            raise MetadataWorkflowError(msg)
        candidate.status = MetadataCandidateStatus.REJECTED
        candidate.rejected_at = datetime.now(UTC)
        review_event(
            session,
            item_id,
            MetadataReviewAction.REJECTED,
            actor,
            candidate=candidate,
            details=candidate.scoring_explanation,
        )

    database.run_transaction(reject)


def list_candidates(
    database: KatalogDatabase,
    *,
    item_id: int | None,
    root_id: int | None,
    media_kind: ZaisanKind | None,
    status: MetadataCandidateStatus | None,
    min_confidence: float | None,
    max_confidence: float | None,
) -> tuple[CandidateView, ...]:
    def load(session: Session) -> tuple[CandidateView, ...]:
        statement: Select[tuple[MetadataCandidate]] = select(MetadataCandidate).join(Zaisan)
        if item_id is not None:
            statement = statement.where(MetadataCandidate.library_item_id == item_id)
        if root_id is not None:
            statement = statement.where(Zaisan.library_root_id == root_id)
        if media_kind is not None:
            statement = statement.where(Zaisan.item_kind == media_kind)
        if status is not None:
            statement = statement.where(MetadataCandidate.status == status)
        if min_confidence is not None:
            statement = statement.where(MetadataCandidate.confidence >= min_confidence)
        if max_confidence is not None:
            statement = statement.where(MetadataCandidate.confidence <= max_confidence)
        return tuple(
            candidate_view(candidate, item)
            for candidate, item in session.execute(
                statement.with_only_columns(MetadataCandidate, Zaisan).order_by(
                    MetadataCandidate.confidence.desc(),
                    MetadataCandidate.library_item_id,
                    MetadataCandidate.id,
                )
            ).all()
        )

    return database.run_transaction(load)


def candidate_view(candidate: MetadataCandidate, item: Zaisan | None = None) -> CandidateView:
    library_item = item or candidate.library_item
    explanation = tuple(
        {str(key): value for key, value in part.items() if isinstance(value, (str, float))}
        for part in candidate.scoring_explanation
    )
    return CandidateView(
        id=candidate.id,
        library_item_id=candidate.library_item_id,
        item_title=library_item.title,
        item_release_year=library_item.release_year,
        provider=candidate.provider,
        provider_id=candidate.provider_id,
        media_kind=candidate.provider_media_kind,
        title=candidate.provider_title,
        original_title=candidate.provider_original_title,
        release_year=candidate.provider_release_year,
        confidence=candidate.confidence,
        explanation=explanation,
        status=candidate.status,
    )


def candidate_is_unchanged(
    candidate: MetadataCandidate, result: SearchResult, provider_year: int | None
) -> bool:
    return (
        candidate.provider_title == result.title
        and candidate.provider_original_title == result.original_title
        and candidate.provider_release_year == provider_year
    )

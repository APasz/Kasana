"""Metadata binding decisions, locks, and auditable review history."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import cast

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from kasana.katalog.database import KatalogDatabase
from kasana.katalog.metadata.scoring import ScoredSearchResult, ScorePart, library_kind
from kasana.katalog.models import (
    JSONObject,
    JSONValue,
    MetadataBinding,
    MetadataCandidate,
    MetadataCandidateStatus,
    MetadataField,
    MetadataMatchStatus,
    MetadataReviewAction,
    MetadataReviewEvent,
    Zaisan,
)
from kasana.shared.metadata import MovieDetails, SeriesDetails

type ProviderDetails = MovieDetails | SeriesDetails


class MetadataWorkflowError(RuntimeError):
    """An administrator-requested metadata operation could not be completed."""


class MetadataBindingView(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    library_item_id: int
    provider: str
    provider_id: str
    status: MetadataMatchStatus
    confidence: float | None
    provider_refreshed_at: datetime | None
    manual_decision: bool


def accept_binding(
    database: KatalogDatabase,
    item_id: int,
    score: ScoredSearchResult,
    details: ProviderDetails,
    *,
    actor: str,
    action: MetadataReviewAction,
    manual: bool,
) -> MetadataBindingView:
    """Persist a selected provider record and apply only unlocked local fields."""

    def accept(session: Session) -> MetadataBindingView:
        item = require_item(session, item_id)
        reference = details.reference
        binding = session.scalar(
            select(MetadataBinding).where(
                MetadataBinding.library_item_id == item_id,
                MetadataBinding.provider == reference.provider,
            )
        )
        now = datetime.now(UTC)
        if binding is None:
            binding = MetadataBinding(
                library_item_id=item_id,
                provider=reference.provider,
                provider_id=reference.raw_id,
                provider_media_kind=library_kind(details.media_kind),
                status=MetadataMatchStatus.MATCHED,
                scoring_explanation=[],
                provider_external_ids=[],
            )
            session.add(binding)
        binding.provider_id = reference.raw_id
        binding.provider_media_kind = library_kind(details.media_kind)
        binding.status = MetadataMatchStatus.MATCHED
        binding.confidence = score.confidence
        binding.scoring_explanation = score_explanation(score.explanation)
        binding.provider_title = details.title
        binding.provider_original_title = details.original_title
        binding.provider_release_year = details.release_date.year if details.release_date else None
        binding.provider_original_language = details.original_language
        binding.provider_external_ids = [
            {"namespace": identifier.namespace, "value": identifier.value}
            for identifier in details.external_ids
        ]
        binding.provider_refreshed_at = now
        binding.accepted_at = now
        binding.manual_decision = manual
        apply_unlocked_metadata(item, details)
        candidate = session.scalar(
            select(MetadataCandidate).where(
                MetadataCandidate.library_item_id == item_id,
                MetadataCandidate.provider == reference.provider,
                MetadataCandidate.provider_id == reference.raw_id,
            )
        )
        if candidate is not None:
            candidate.status = MetadataCandidateStatus.ACCEPTED
        session.flush()
        review_event(
            session,
            item_id,
            action,
            actor,
            binding=binding,
            candidate=candidate,
            details=binding.scoring_explanation,
        )
        return binding_view(binding)

    return database.run_transaction(accept)


def require_item(session: Session, item_id: int) -> Zaisan:
    item = session.get(Zaisan, item_id)
    if item is None:
        msg = f"Library item {item_id} does not exist."
        raise MetadataWorkflowError(msg)
    return item


def score_explanation(parts: Sequence[ScorePart]) -> list[JSONObject]:
    return [cast(JSONObject, part.as_json()) for part in parts]


def ignore_item(database: KatalogDatabase, item_id: int, actor: str) -> MetadataBindingView:
    def ignore(session: Session) -> MetadataBindingView:
        item = require_item(session, item_id)
        binding = session.scalar(
            select(MetadataBinding).where(
                MetadataBinding.library_item_id == item_id,
                MetadataBinding.provider == "katalog",
            )
        )
        now = datetime.now(UTC)
        if binding is None:
            binding = MetadataBinding(
                library_item_id=item_id,
                provider="katalog",
                provider_id=str(item_id),
                provider_media_kind=item.item_kind,
                status=MetadataMatchStatus.IGNORED,
                scoring_explanation=[],
                provider_external_ids=[],
                manual_decision=True,
                accepted_at=now,
            )
            session.add(binding)
        else:
            binding.status = MetadataMatchStatus.IGNORED
            binding.manual_decision = True
            binding.accepted_at = now
        session.flush()
        review_event(session, item_id, MetadataReviewAction.IGNORED, actor, binding=binding)
        return binding_view(binding)

    return database.run_transaction(ignore)


def unmatch_item(database: KatalogDatabase, item_id: int, actor: str) -> None:
    def unmatch(session: Session) -> None:
        require_item(session, item_id)
        bindings = session.scalars(
            select(MetadataBinding).where(MetadataBinding.library_item_id == item_id)
        ).all()
        for binding in bindings:
            binding.status = MetadataMatchStatus.UNMATCHED
            binding.manual_decision = False
            review_event(session, item_id, MetadataReviewAction.UNMATCHED, actor, binding=binding)
        for candidate in session.scalars(
            select(MetadataCandidate).where(
                MetadataCandidate.library_item_id == item_id,
                MetadataCandidate.status == MetadataCandidateStatus.ACCEPTED,
            )
        ).all():
            candidate.status = MetadataCandidateStatus.SUGGESTED

    database.run_transaction(unmatch)


def matched_binding(database: KatalogDatabase, item_id: int) -> MetadataBinding:
    def load(session: Session) -> MetadataBinding:
        binding = session.scalar(
            select(MetadataBinding)
            .where(
                MetadataBinding.library_item_id == item_id,
                MetadataBinding.status == MetadataMatchStatus.MATCHED,
            )
            .order_by(MetadataBinding.manual_decision.desc(), MetadataBinding.id)
        )
        if binding is None:
            msg = f"Item {item_id} has no matched provider binding."
            raise MetadataWorkflowError(msg)
        session.expunge(binding)
        return binding

    return database.run_transaction(load)


def refresh_binding(
    database: KatalogDatabase, binding_id: int, details: ProviderDetails
) -> MetadataBindingView:
    def refresh(session: Session) -> MetadataBindingView:
        binding = session.get(MetadataBinding, binding_id)
        if binding is None:
            msg = f"Metadata binding {binding_id} does not exist."
            raise MetadataWorkflowError(msg)
        item = require_item(session, binding.library_item_id)
        binding.provider_title = details.title
        binding.provider_original_title = details.original_title
        binding.provider_release_year = details.release_date.year if details.release_date else None
        binding.provider_original_language = details.original_language
        binding.provider_external_ids = [
            {"namespace": identifier.namespace, "value": identifier.value}
            for identifier in details.external_ids
        ]
        binding.provider_refreshed_at = datetime.now(UTC)
        apply_unlocked_metadata(item, details)
        review_event(session, item.id, MetadataReviewAction.REFRESHED, "automatic", binding=binding)
        return binding_view(binding)

    return database.run_transaction(refresh)


def apply_unlocked_metadata(item: Zaisan, details: ProviderDetails) -> None:
    locks = locked_fields(item)
    if MetadataField.TITLE not in locks:
        item.title = details.title
    if MetadataField.SORT_TITLE not in locks:
        item.sort_title = details.title
    if MetadataField.RELEASE_DATE not in locks and details.release_date is not None:
        item.release_date = details.release_date
        item.release_year = details.release_date.year
    if MetadataField.OVERVIEW not in locks and details.overview is not None:
        item.overview = details.overview


def locked_fields(item: Zaisan) -> frozenset[MetadataField]:
    fields: set[MetadataField] = set()
    for value in item.locked_metadata_fields:
        try:
            fields.add(MetadataField(value))
        except ValueError:
            continue
    return frozenset(fields)


def review_event(
    session: Session,
    item_id: int,
    action: MetadataReviewAction,
    actor: str,
    *,
    binding: MetadataBinding | None = None,
    candidate: MetadataCandidate | None = None,
    details: Sequence[Mapping[str, JSONValue]] = (),
) -> None:
    session.add(
        MetadataReviewEvent(
            library_item_id=item_id,
            metadata_binding_id=binding.id if binding is not None else None,
            metadata_candidate_id=candidate.id if candidate is not None else None,
            action=action,
            actor=actor,
            details=[dict(part) for part in details],
            occurred_at=datetime.now(UTC),
        )
    )


def binding_view(binding: MetadataBinding) -> MetadataBindingView:
    return MetadataBindingView(
        id=binding.id,
        library_item_id=binding.library_item_id,
        provider=binding.provider,
        provider_id=binding.provider_id,
        status=binding.status,
        confidence=binding.confidence,
        provider_refreshed_at=binding.provider_refreshed_at,
        manual_decision=binding.manual_decision,
    )

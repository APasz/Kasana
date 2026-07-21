"""Pure metadata candidate scoring and auto-match safety rules."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from re import Pattern
from typing import Literal

from kasana.katalog.models import ZaisanKind
from kasana.shared.metadata import ProviderMediaKind, SearchResult

type SupportedItemKind = Literal[ZaisanKind.MOVIE, ZaisanKind.SERIES]

_TOKEN_PATTERN: Pattern[str] = re.compile(r"[^\w]+", re.UNICODE)


@dataclass(frozen=True)
class MatchThresholds:
    """Confidence limits applied after provider results have been scored."""

    auto_match: float = 0.94
    suggestion: float = 0.70
    ambiguity_margin: float = 0.08

    def __post_init__(self) -> None:
        if not 0.0 <= self.suggestion <= self.auto_match <= 1.0:
            msg = "Metadata thresholds must satisfy 0 <= suggestion <= auto_match <= 1."
            raise ValueError(msg)
        if not 0.0 <= self.ambiguity_margin <= 1.0:
            msg = "The metadata ambiguity margin must be between zero and one."
            raise ValueError(msg)


DEFAULT_THRESHOLDS: MatchThresholds = MatchThresholds()


@dataclass(frozen=True)
class ItemMatchContext:
    """Local evidence used to evaluate one provider result."""

    item_id: int
    title: str
    release_year: int | None
    item_kind: SupportedItemKind
    root_tags: frozenset[str]
    directory_title: str | None
    path_year: int | None
    external_identifiers: frozenset[tuple[str, str]]


@dataclass(frozen=True)
class ScorePart:
    signal: str
    contribution: float
    detail: str

    def as_json(self) -> dict[str, str | float]:
        return {
            "signal": self.signal,
            "contribution": round(self.contribution, 4),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ScoredSearchResult:
    result: SearchResult
    confidence: float
    explanation: tuple[ScorePart, ...]
    has_exact_title: bool
    has_year_confirmation: bool
    has_external_identifier: bool

    @property
    def auto_safe(self) -> bool:
        return self.has_external_identifier or (self.has_exact_title and self.has_year_confirmation)


def normalise_title(value: str) -> str:
    """Normalise human titles into stable tokens for deterministic comparison."""

    return " ".join(token for token in _TOKEN_PATTERN.sub(" ", value.casefold()).split() if token)


def title_similarity(left: str, right: str) -> float:
    normalised_left: str = normalise_title(left)
    normalised_right: str = normalise_title(right)
    if not normalised_left or not normalised_right:
        return 0.0
    return SequenceMatcher[str](None, normalised_left, normalised_right, autojunk=False).ratio()


def score_search_result(context: ItemMatchContext, result: SearchResult) -> ScoredSearchResult:
    """Score one provider result without reading state or mutating persistence."""

    parts: list[ScorePart] = []
    expected_kind = provider_kind(context.item_kind)
    if result.media_kind is not expected_kind:
        parts.append(
            ScorePart("media_kind", -0.5, "Provider media kind differs from the item kind.")
        )
        return ScoredSearchResult(result, 0.0, tuple(parts), False, False, False)

    primary_similarity: float = title_similarity(context.title, result.title)
    primary_contribution: float = 0.65 * primary_similarity
    parts.append(
        ScorePart(
            "title_similarity",
            primary_contribution,
            f"Normalised title similarity is {primary_similarity:.3f}.",
        )
    )
    exact_title = normalise_title(context.title) == normalise_title(result.title)

    if result.original_title is not None:
        original_similarity = title_similarity(context.title, result.original_title)
        parts.append(
            ScorePart(
                "original_title_similarity",
                0.1 * original_similarity,
                f"Original-title similarity is {original_similarity:.3f}.",
            )
        )
    else:
        original_similarity = 0.0

    parts.append(ScorePart("media_kind", 0.1, "Provider media kind matches the item kind."))
    provider_year = result.release_date.year if result.release_date is not None else None
    year_confirmation = False
    if context.release_year is not None and provider_year is not None:
        if context.release_year == provider_year:
            parts.append(
                ScorePart("release_year", 0.2, f"Release year matches {context.release_year}.")
            )
            year_confirmation = True
        else:
            parts.append(
                ScorePart(
                    "release_year",
                    -0.18,
                    f"Release years differ: local {context.release_year}, "
                    f"provider {provider_year}.",
                )
            )
    elif context.path_year is not None and provider_year is not None:
        if context.path_year == provider_year:
            parts.append(ScorePart("path_year", 0.12, f"Path year matches {provider_year}."))
            year_confirmation = True
        else:
            parts.append(
                ScorePart(
                    "path_year",
                    -0.1,
                    f"Path year {context.path_year} differs from provider {provider_year}.",
                )
            )

    if context.directory_title is not None:
        directory_similarity = title_similarity(context.directory_title, result.title)
        parts.append(
            ScorePart(
                "directory_title",
                0.05 * directory_similarity,
                f"Directory-title similarity is {directory_similarity:.3f}.",
            )
        )

    if "anime" in context.root_tags:
        if result.original_language == "ja":
            parts.append(
                ScorePart("anime_language", 0.04, "Anime root and Japanese original language.")
            )
        elif result.original_language is not None:
            parts.append(
                ScorePart("anime_language", -0.02, "Anime root but non-Japanese original language.")
            )

    external_match = (
        result.reference.provider,
        result.reference.raw_id,
    ) in context.external_identifiers
    if external_match:
        parts.append(ScorePart("external_identifier", 0.5, "Existing provider identifier matches."))

    confidence = min(1.0, max(0.0, sum(part.contribution for part in parts)))
    return ScoredSearchResult(
        result=result,
        confidence=round(confidence, 6),
        explanation=tuple(parts),
        has_exact_title=exact_title,
        has_year_confirmation=year_confirmation,
        has_external_identifier=external_match,
    )


def safe_auto_candidate(
    scored: tuple[ScoredSearchResult, ...], thresholds: MatchThresholds
) -> ScoredSearchResult | None:
    """Return a uniquely safe high-confidence candidate, if one exists."""

    if not scored:
        return None
    first = scored[0]
    if first.confidence < thresholds.auto_match or not first.auto_safe:
        return None
    if len(scored) > 1 and first.confidence - scored[1].confidence < thresholds.ambiguity_margin:
        return None
    return first


def provider_kind(item_kind: SupportedItemKind) -> ProviderMediaKind:
    if item_kind is ZaisanKind.MOVIE:
        return ProviderMediaKind.MOVIE
    return ProviderMediaKind.SERIES


def library_kind(provider_kind: ProviderMediaKind) -> ZaisanKind:
    if provider_kind is ProviderMediaKind.MOVIE:
        return ZaisanKind.MOVIE
    if provider_kind is ProviderMediaKind.SERIES:
        return ZaisanKind.SERIES
    msg = f"Provider media kind {provider_kind.value!r} is not supported for catalogue matching."
    raise ValueError(msg)


def directory_title(paths: tuple[Path, ...], item_kind: SupportedItemKind) -> str | None:
    if not paths:
        return None
    parents = {path.parent for path in paths}
    if len(parents) != 1:
        return None
    directory = next(iter(parents))
    if item_kind is ZaisanKind.SERIES:
        return directory.parent.name or None
    return directory.name or None


def path_year(paths: tuple[Path, ...]) -> int | None:
    for path in paths:
        match = re.search(r"(?<!\d)((?:18|19|20)\d{2})(?!\d)", str(path))
        if match is not None:
            return int(match.group(1))
    return None

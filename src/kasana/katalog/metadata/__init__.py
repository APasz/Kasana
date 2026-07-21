"""Katalog metadata matching, review, refresh, and artwork workflows."""

from kasana.katalog.metadata.artwork import ArtworkCacheView
from kasana.katalog.metadata.candidates import CandidateView
from kasana.katalog.metadata.matching import MetadataWorkflow, SearchOutcome
from kasana.katalog.metadata.refresh import MetadataProvider
from kasana.katalog.metadata.review import MetadataBindingView, MetadataWorkflowError
from kasana.katalog.metadata.scoring import (
    ItemMatchContext,
    MatchThresholds,
    ScoredSearchResult,
    ScorePart,
    normalise_title,
    score_search_result,
    title_similarity,
)

__all__ = [
    "ArtworkCacheView",
    "CandidateView",
    "ItemMatchContext",
    "MatchThresholds",
    "MetadataBindingView",
    "MetadataProvider",
    "MetadataWorkflow",
    "MetadataWorkflowError",
    "ScorePart",
    "ScoredSearchResult",
    "SearchOutcome",
    "normalise_title",
    "score_search_result",
    "title_similarity",
]

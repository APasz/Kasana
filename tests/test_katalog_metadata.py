from __future__ import annotations

import asyncio
from collections.abc import Generator
from datetime import date
from pathlib import Path

import pytest
from pydantic import AnyHttpUrl
from sqlalchemy import select
from sqlalchemy.orm import Session

from kasana.katalog.database import KatalogDatabase
from kasana.katalog.metadata import (
    ItemMatchContext,
    MatchThresholds,
    MetadataWorkflow,
    score_search_result,
)
from kasana.katalog.models import (
    CachedArtwork,
    MetadataBinding,
    MetadataCandidateStatus,
    MetadataField,
    Zaisan,
    ZaisanKind,
)
from kasana.katalog.services import create_library_item, create_library_root
from kasana.shared.metadata import (
    ArtworkContent,
    ArtworkKind,
    ArtworkReference,
    MovieDetails,
    ProviderCapability,
    ProviderMediaKind,
    ProviderReference,
    SearchQuery,
    SearchResult,
    SeriesDetails,
)


@pytest.fixture
def database(tmp_path: Path) -> Generator[KatalogDatabase]:
    result = KatalogDatabase(tmp_path / "metadata.sqlite3")
    result.create_schema()
    yield result
    result.close()


class _FakeProvider:
    provider_name = "fake"

    def __init__(
        self,
        results: tuple[SearchResult, ...],
        details: dict[str, MovieDetails | SeriesDetails],
        artwork: ArtworkContent | None = None,
    ) -> None:
        self.results = results
        self.details = details
        self.artwork = artwork
        self.search_calls = 0
        self.artwork_calls = 0

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return frozenset(ProviderCapability)

    def supports(self, capability: ProviderCapability) -> bool:
        return capability in self.capabilities

    async def search_movies(self, query: SearchQuery) -> tuple[SearchResult, ...]:
        del query
        self.search_calls += 1
        return self.results

    async def search_series(self, query: SearchQuery) -> tuple[SearchResult, ...]:
        del query
        self.search_calls += 1
        return self.results

    async def get_movie(self, reference: ProviderReference) -> MovieDetails:
        details = self.details[reference.raw_id]
        assert isinstance(details, MovieDetails)
        return details

    async def get_series(self, reference: ProviderReference) -> SeriesDetails:
        details = self.details[reference.raw_id]
        assert isinstance(details, SeriesDetails)
        return details

    async def get_artwork(self, reference: ArtworkReference) -> ArtworkContent:
        del reference
        self.artwork_calls += 1
        assert self.artwork is not None
        return self.artwork


def _search_result(
    provider_id: str,
    title: str,
    *,
    year: int | None = None,
    language: str | None = None,
    kind: ProviderMediaKind = ProviderMediaKind.MOVIE,
    poster: ArtworkReference | None = None,
) -> SearchResult:
    return SearchResult(
        reference=ProviderReference(provider="fake", raw_id=provider_id),
        media_kind=kind,
        title=title,
        original_title=title,
        translated_title=title,
        release_date=date(year, 1, 1) if year is not None else None,
        original_language=language,
        poster=poster,
    )


def _movie_details(
    provider_id: str,
    title: str,
    *,
    year: int,
    overview: str = "Provider overview",
    poster: ArtworkReference | None = None,
) -> MovieDetails:
    return MovieDetails(
        reference=ProviderReference(provider="fake", raw_id=provider_id),
        title=title,
        original_title=title,
        translated_title=title,
        release_date=date(year, 1, 1),
        overview=overview,
        poster=poster,
    )


def _create_movie(
    database: KatalogDatabase,
    path: Path,
    *,
    title: str,
    year: int | None,
    tags: frozenset[str] = frozenset(),
    locks: frozenset[MetadataField] = frozenset(),
) -> int:
    def create(session: Session) -> int:
        root = create_library_root(
            session,
            path=path,
            expected_media_kind=ZaisanKind.MOVIE,
            default_tags=tags,
        )
        return create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title=title,
            release_year=year,
            locked_metadata_fields=locks,
        ).id

    return database.run_transaction(create)


def _workflow(database: KatalogDatabase, cache_path: Path) -> MetadataWorkflow:
    return MetadataWorkflow(
        database,
        artwork_cache_path=cache_path,
        thresholds=MatchThresholds(auto_match=0.94, suggestion=0.7, ambiguity_margin=0.08),
    )


def test_scoring_is_deterministic_for_exact_titles_remakes_and_anime() -> None:
    context = ItemMatchContext(
        item_id=1,
        title="Spirited Away",
        release_year=2001,
        item_kind=ZaisanKind.MOVIE,
        root_tags=frozenset(),
        directory_title="Spirited Away",
        path_year=2001,
        external_identifiers=frozenset(),
    )
    exact = score_search_result(context, _search_result("1", "Spirited Away", year=2001))
    remake = score_search_result(context, _search_result("2", "Spirited Away", year=2003))
    false_positive = score_search_result(context, _search_result("3", "Away We Go", year=None))

    assert (
        exact.confidence
        == score_search_result(context, _search_result("1", "Spirited Away", year=2001)).confidence
    )
    assert exact.auto_safe
    assert exact.confidence >= 0.94
    assert remake.confidence < exact.confidence
    assert not remake.auto_safe
    assert not false_positive.auto_safe
    assert {part.signal for part in exact.explanation} >= {
        "title_similarity",
        "original_title_similarity",
        "release_year",
        "media_kind",
        "directory_title",
    }

    anime_context = ItemMatchContext(
        item_id=2,
        title="Galaxy Express",
        release_year=None,
        item_kind=ZaisanKind.MOVIE,
        root_tags=frozenset({"anime"}),
        directory_title=None,
        path_year=None,
        external_identifiers=frozenset(),
    )
    japanese = score_search_result(
        anime_context, _search_result("4", "Galaxy Express", language="ja")
    )
    english = score_search_result(
        anime_context, _search_result("5", "Galaxy Express", language="en")
    )
    assert japanese.confidence > english.confidence


async def test_exact_match_auto_accepts_and_applies_unlocked_metadata(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    item_id = _create_movie(database, tmp_path / "Movies", title="Spirited Away", year=2001)
    result = _search_result("11", "Spirited Away", year=2001)
    provider = _FakeProvider((result,), {"11": _movie_details("11", "Spirited Away", year=2001)})
    workflow = _workflow(database, tmp_path / "cache")

    assert (await workflow.discover_unmatched())[0].item_id == item_id
    outcome = await workflow.search_item(item_id, (provider,))

    assert outcome.auto_matched_provider_id == "11"
    assert provider.search_calls == 1
    assert await workflow.discover_unmatched() == ()
    binding = await workflow.refresh_item(item_id, (provider,))
    assert binding.manual_decision is False
    assert binding.provider_id == "11"


async def test_remake_ambiguity_and_title_only_results_require_review(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    remake_item_id = _create_movie(database, tmp_path / "Movies", title="Dune", year=2021)
    same_title = (
        _search_result("21", "Dune", year=2021),
        _search_result("22", "Dune", year=2021),
    )
    provider = _FakeProvider(
        same_title,
        {
            "21": _movie_details("21", "Dune", year=2021),
            "22": _movie_details("22", "Dune", year=2021),
        },
    )
    workflow = _workflow(database, tmp_path / "cache")

    remake_outcome = await workflow.search_item(remake_item_id, (provider,))

    assert remake_outcome.auto_matched_provider_id is None
    assert len(remake_outcome.candidates) == 2
    assert all(
        candidate.status is MetadataCandidateStatus.SUGGESTED
        for candidate in remake_outcome.candidates
    )

    title_only_item_id = _create_movie(
        database, tmp_path / "Uncertain Movies", title="The Gift", year=None
    )
    title_only = _search_result("23", "The Gift", year=None)
    title_provider = _FakeProvider(
        (title_only,), {"23": _movie_details("23", "The Gift", year=2015)}
    )

    title_only_outcome = await workflow.search_item(title_only_item_id, (title_provider,))

    assert title_only_outcome.auto_matched_provider_id is None
    assert title_only_outcome.candidates[0].confidence < 0.94


async def test_rejected_candidate_is_not_automatically_reintroduced(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    item_id = _create_movie(database, tmp_path / "Movies", title="Arrival", year=2016)
    result = _search_result("31", "Arrival", year=2016).model_copy(update={"original_title": None})
    provider = _FakeProvider((result,), {"31": _movie_details("31", "Arrival", year=2016)})
    cautious = MetadataWorkflow(
        database,
        thresholds=MatchThresholds(auto_match=0.96, suggestion=0.7, ambiguity_margin=0.08),
        artwork_cache_path=tmp_path / "cache",
    )

    first = await cautious.search_item(item_id, (provider,))
    assert first.auto_matched_provider_id is None
    await cautious.reject_candidate(item_id, "fake", "31")

    second = await _workflow(database, tmp_path / "cache").search_item(item_id, (provider,))

    assert second.auto_matched_provider_id is None
    candidates = await cautious.list_candidates(item_id=item_id)
    assert candidates[0].status is MetadataCandidateStatus.REJECTED


async def test_manual_match_and_refresh_respect_metadata_locks(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    item_id = _create_movie(
        database,
        tmp_path / "Movies",
        title="Local title",
        year=1999,
        locks=frozenset({MetadataField.TITLE, MetadataField.OVERVIEW}),
    )
    provider = _FakeProvider(
        (),
        {"41": _movie_details("41", "Provider title", year=2000, overview="Provider overview")},
    )
    workflow = _workflow(database, tmp_path / "cache")

    binding = await workflow.match_item(item_id, provider, "41")
    provider.details["41"] = _movie_details(
        "41", "Replacement title", year=2001, overview="Replacement overview"
    )
    refreshed = await workflow.refresh_item(item_id, (provider,))

    assert binding.manual_decision is True
    assert refreshed.manual_decision is True

    def loaded(session: Session) -> tuple[Zaisan, MetadataBinding]:
        item = session.get(Zaisan, item_id)
        stored_binding = session.scalar(select(MetadataBinding))
        assert item is not None
        assert stored_binding is not None
        return item, stored_binding

    item, stored_binding = database.run_transaction(loaded)
    assert item.title == "Local title"
    assert item.overview is None
    assert item.release_year == 2001
    assert stored_binding.provider_title == "Replacement title"
    assert stored_binding.manual_decision is True


def _poster_reference(revision: str = "/poster-v1.png") -> ArtworkReference:
    return ArtworkReference(
        provider="fake",
        kind=ArtworkKind.POSTER,
        raw_path=revision,
        source_url=AnyHttpUrl(f"https://images.example.test{revision}"),
    )


async def test_artwork_cache_deduplicates_and_prunes_unmatched_records(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    item_id = _create_movie(database, tmp_path / "Movies", title="Paprika", year=2006)
    poster = _poster_reference()
    artwork_content = ArtworkContent(
        reference=poster,
        content=b"\x89PNG\r\n\x1a\nminimal",
        media_type="image/png",
    )
    provider = _FakeProvider(
        (_search_result("51", "Paprika", year=2006, poster=poster),),
        {"51": _movie_details("51", "Paprika", year=2006, poster=poster)},
        artwork_content,
    )
    workflow = _workflow(database, tmp_path / "cache")

    await workflow.search_item(item_id, (provider,))
    first = await workflow.fetch_posters((provider,))
    second = await workflow.fetch_posters((provider,))

    assert len(first) == len(second) == 1
    assert provider.artwork_calls == 1
    cache_path = tmp_path / "cache" / first[0].cache_path
    assert cache_path.is_file()

    await workflow.unmatch_item(item_id)
    removed_files, removed_bytes = await workflow.prune_artwork()

    assert (removed_files, removed_bytes) == (1, len(artwork_content.content))
    assert not cache_path.exists()
    assert database.run_transaction(lambda session: session.scalar(select(CachedArtwork))) is None


async def test_cancelled_artwork_fetch_leaves_no_partial_file(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    item_id = _create_movie(database, tmp_path / "Movies", title="Perfect Blue", year=1997)
    poster = _poster_reference("/cancelled.png")

    class CancelledProvider(_FakeProvider):
        async def get_artwork(self, reference: ArtworkReference) -> ArtworkContent:
            del reference
            raise asyncio.CancelledError

    provider = CancelledProvider(
        (_search_result("61", "Perfect Blue", year=1997, poster=poster),),
        {"61": _movie_details("61", "Perfect Blue", year=1997, poster=poster)},
    )
    workflow = _workflow(database, tmp_path / "cache")
    await workflow.search_item(item_id, (provider,))

    with pytest.raises(asyncio.CancelledError):
        await workflow.fetch_posters((provider,))

    cache_path = tmp_path / "cache"
    assert not tuple(path for path in cache_path.rglob("*") if path.is_file())


async def test_auto_match_commits_each_bounded_item_before_a_later_failure(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    def create(session: Session) -> tuple[int, int]:
        root = create_library_root(
            session,
            path=tmp_path / "Movies",
            expected_media_kind=ZaisanKind.MOVIE,
        )
        first = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="First Film",
            release_year=2001,
        )
        second = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="Second Film",
            release_year=2002,
        )
        return first.id, second.id

    first_id, _ = database.run_transaction(create)

    class FailingProvider(_FakeProvider):
        async def search_movies(self, query: SearchQuery) -> tuple[SearchResult, ...]:
            del query
            self.search_calls += 1
            if self.search_calls == 2:
                raise RuntimeError("provider interrupted")
            return self.results

    provider = FailingProvider(
        (_search_result("71", "First Film", year=2001),),
        {"71": _movie_details("71", "First Film", year=2001)},
    )
    workflow = MetadataWorkflow(
        database,
        batch_size=1,
        artwork_cache_path=tmp_path / "cache",
    )

    with pytest.raises(RuntimeError, match="interrupted"):
        await workflow.auto_match((provider,))

    binding = database.run_transaction(
        lambda session: session.scalar(
            select(MetadataBinding).where(MetadataBinding.library_item_id == first_id)
        )
    )
    assert binding is not None

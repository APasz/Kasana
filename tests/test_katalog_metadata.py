from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from typing import Literal

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
from kasana.katalog.metadata.review import MetadataIdentityConflictError
from kasana.katalog.models import (
    CachedArtwork,
    CachedArtworkKind,
    MetadataBinding,
    MetadataCandidate,
    MetadataCandidateStatus,
    MetadataField,
    MetadataMatchStatus,
    Zaisan,
    ZaisanKind,
)
from kasana.katalog.services import create_library_item, create_library_root
from kasana.kourier.errors import KourierError
from kasana.shared.metadata import (
    ArtworkContent,
    ArtworkKind,
    ArtworkReference,
    EpisodeDetails,
    ExternalIdentifier,
    MovieDetails,
    PosterListing,
    PosterLookup,
    ProviderCapability,
    ProviderErrorCategory,
    ProviderMediaKind,
    ProviderReference,
    SearchQuery,
    SearchResult,
    SeasonDetails,
    SeriesDetails,
)


class _FakeProvider:
    provider_name = "fake"

    def __init__(
        self,
        results: tuple[SearchResult, ...],
        details: dict[str, MovieDetails | SeriesDetails],
        artwork: ArtworkContent | None = None,
        posters: tuple[ArtworkReference, ...] = (),
        season_details: dict[tuple[str, int], SeasonDetails] | None = None,
        *,
        artwork_error: KourierError | None = None,
        poster_listing_error: KourierError | None = None,
    ) -> None:
        self.results = results
        self.details = details
        self.artwork = artwork
        self.posters = posters
        self.search_calls = 0
        self.last_search_query: SearchQuery | None = None
        self.artwork_calls = 0
        self.poster_list_calls = 0
        self.season_details = season_details or {}
        self.season_calls: list[tuple[ProviderReference, int]] = []
        self.missing_seasons: set[tuple[str, int]] = set()
        self.artwork_error = artwork_error
        self.poster_listing_error = poster_listing_error

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return frozenset(ProviderCapability)

    def supports(self, capability: ProviderCapability) -> bool:
        return capability in self.capabilities

    async def search_movies(self, query: SearchQuery) -> tuple[SearchResult, ...]:
        self.last_search_query = query
        self.search_calls += 1
        return self.results

    async def search_series(self, query: SearchQuery) -> tuple[SearchResult, ...]:
        self.last_search_query = query
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

    async def get_season(
        self, series_reference: ProviderReference, season_number: int
    ) -> SeasonDetails:
        self.season_calls.append((series_reference, season_number))
        if (series_reference.raw_id, season_number) in self.missing_seasons:
            raise KourierError(
                ProviderErrorCategory.NOT_FOUND,
                "Provider season is unavailable.",
                provider=self.provider_name,
                status_code=404,
            )
        return self.season_details[(series_reference.raw_id, season_number)]

    async def get_artwork(self, reference: ArtworkReference) -> ArtworkContent:
        del reference
        self.artwork_calls += 1
        if self.artwork_error is not None:
            raise self.artwork_error
        assert self.artwork is not None
        return self.artwork

    async def list_posters(
        self, reference: ProviderReference, media_kind: ProviderMediaKind
    ) -> tuple[ArtworkReference, ...]:
        assert reference.provider == self.provider_name
        assert media_kind is ProviderMediaKind.MOVIE
        self.poster_list_calls += 1
        if self.poster_listing_error is not None:
            raise self.poster_listing_error
        return self.posters


class _ConcurrencyTrackingPosterProvider(_FakeProvider):
    """Expose the maximum concurrent variant requests made by the artwork cache."""

    def __init__(
        self,
        results: tuple[SearchResult, ...],
        details: dict[str, MovieDetails | SeriesDetails],
        artwork: ArtworkContent,
    ) -> None:
        super().__init__(results, details, artwork)
        self.active_poster_lookups = 0
        self.maximum_poster_lookups = 0

    async def list_posters(
        self, reference: ProviderReference, media_kind: ProviderMediaKind
    ) -> tuple[ArtworkReference, ...]:
        self.active_poster_lookups += 1
        self.maximum_poster_lookups = max(self.maximum_poster_lookups, self.active_poster_lookups)
        try:
            await asyncio.sleep(0)
            return await super().list_posters(reference, media_kind)
        finally:
            self.active_poster_lookups -= 1


class _SupplementalPosterProvider(_FakeProvider):
    provider_name = "fanart"

    def __init__(
        self,
        artwork: ArtworkContent,
        posters: tuple[ArtworkReference, ...],
        *,
        provider_id: str,
        artwork_error: KourierError | None = None,
        poster_listing_error: KourierError | None = None,
    ) -> None:
        super().__init__(
            (),
            {},
            artwork,
            posters,
            artwork_error=artwork_error,
            poster_listing_error=poster_listing_error,
        )
        self.provider_id = provider_id
        self.lookups: list[PosterLookup] = []

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return frozenset(
            {
                ProviderCapability.GET_ARTWORK,
                ProviderCapability.LIST_POSTERS_BY_EXTERNAL_ID,
            }
        )

    async def list_posters_by_external_id(self, lookup: PosterLookup) -> PosterListing:
        self.lookups.append(lookup)
        self.poster_list_calls += 1
        if self.poster_listing_error is not None:
            raise self.poster_listing_error
        return PosterListing(
            provider=self.provider_name,
            provider_id=self.provider_id,
            posters=self.posters,
        )


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
    external_ids: tuple[ExternalIdentifier, ...] = (),
) -> MovieDetails:
    return MovieDetails(
        reference=ProviderReference(provider="fake", raw_id=provider_id),
        title=title,
        original_title=title,
        translated_title=title,
        release_date=date(year, 1, 1),
        overview=overview,
        poster=poster,
        external_ids=external_ids,
    )


def _series_details(
    provider_id: str,
    title: str,
    *,
    poster: ArtworkReference | None = None,
    external_ids: tuple[ExternalIdentifier, ...] = (),
) -> SeriesDetails:
    return SeriesDetails(
        reference=ProviderReference(provider="fake", raw_id=provider_id),
        title=title,
        original_title=title,
        translated_title=title,
        poster=poster,
        external_ids=external_ids,
    )


def _season_details(
    series_provider_id: str,
    season_provider_id: str,
    season_number: int,
    *,
    poster: ArtworkReference | None,
    episodes: tuple[EpisodeDetails, ...] = (),
) -> SeasonDetails:
    return SeasonDetails(
        reference=ProviderReference(provider="fake", raw_id=season_provider_id),
        series_reference=ProviderReference(provider="fake", raw_id=series_provider_id),
        season_number=season_number,
        title=f"Season {season_number}",
        poster=poster,
        episodes=episodes,
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


def _workflow(
    database: KatalogDatabase, cache_path: Path, *, artwork_concurrency: int = 4
) -> MetadataWorkflow:
    return MetadataWorkflow(
        database,
        artwork_cache_path=cache_path,
        artwork_concurrency=artwork_concurrency,
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


async def test_manual_metadata_search_does_not_persist_candidates(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    item_id = _create_movie(database, tmp_path / "Movies", title="Local title", year=2000)
    provider = _FakeProvider(
        (_search_result("17", "Correct record", year=2001),),
        {"17": _movie_details("17", "Correct record", year=2001)},
    )

    results = await _workflow(database, tmp_path / "cache").search_item_records(
        item_id, (provider,), query="Correct record"
    )

    assert [result.result.reference.raw_id for result in results] == ["17"]
    assert provider.last_search_query == SearchQuery(query="Correct record")
    bindings = database.run_transaction(
        lambda session: session.scalars(select(MetadataBinding)).all()
    )
    assert bindings == []


async def test_manual_reassignment_replaces_the_active_provider_record(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    item_id = _create_movie(database, tmp_path / "Movies", title="Local title", year=2000)
    provider = _FakeProvider(
        (),
        {
            "41": _movie_details("41", "First record", year=2001),
            "42": _movie_details("42", "Replacement record", year=2002),
        },
    )
    workflow = _workflow(database, tmp_path / "cache")

    await workflow.match_item(item_id, provider, "41")
    await workflow.match_item(item_id, provider, "42")

    def load(session: Session) -> tuple[MetadataBinding, dict[str, MetadataCandidateStatus]]:
        bindings = session.scalars(select(MetadataBinding)).all()
        assert len(bindings) == 1
        candidates = {
            candidate.provider_id: candidate.status
            for candidate in session.scalars(select(MetadataCandidate)).all()
        }
        return bindings[0], candidates

    binding, candidates = database.run_transaction(load)
    assert (binding.provider_id, binding.status, binding.manual_decision) == (
        "42",
        MetadataMatchStatus.MATCHED,
        True,
    )
    assert candidates == {
        "41": MetadataCandidateStatus.SUGGESTED,
        "42": MetadataCandidateStatus.ACCEPTED,
    }


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


async def test_auto_match_retains_a_suggested_candidate_when_metadata_would_collide(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    def create(session: Session) -> int:
        root = create_library_root(
            session,
            path=tmp_path / "Movies",
            expected_media_kind=ZaisanKind.MOVIE,
        )
        create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="Everything Everywhere All at Once",
            release_year=2022,
        )
        return create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="Everything, Everywhere, All At Once",
            release_year=2022,
        ).id

    item_id = database.run_transaction(create)
    result = _search_result("12", "Everything Everywhere All at Once", year=2022)
    provider = _FakeProvider(
        (result,), {"12": _movie_details("12", "Everything Everywhere All at Once", year=2022)}
    )

    outcome = await _workflow(database, tmp_path / "cache").search_item(item_id, (provider,))

    assert outcome.auto_matched_provider_id is None
    assert len(outcome.candidates) == 1
    assert outcome.candidates[0].status is MetadataCandidateStatus.SUGGESTED

    def load(session: Session) -> tuple[Zaisan, MetadataBinding | None]:
        item = session.get(Zaisan, item_id)
        assert item is not None
        binding = session.scalar(
            select(MetadataBinding).where(MetadataBinding.library_item_id == item_id)
        )
        return item, binding

    item, binding = database.run_transaction(load)
    assert item.title == "Everything, Everywhere, All At Once"
    assert binding is None


async def test_manual_match_allows_same_titled_movies_with_different_years(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    def create(session: Session) -> tuple[int, int]:
        root = create_library_root(
            session,
            path=tmp_path / "Movies",
            expected_media_kind=ZaisanKind.MOVIE,
        )
        original = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="Ghostbusters",
            release_year=1984,
        )
        reboot = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="Ghostbusters (2016)",
            sort_title="Ghostbusters (2016)",
            release_year=2016,
        )
        return original.id, reboot.id

    original_id, reboot_id = database.run_transaction(create)
    provider = _FakeProvider(
        (),
        {"7027": _movie_details("7027", "Ghostbusters", year=2016)},
    )

    binding = await _workflow(database, tmp_path / "cache").match_item(reboot_id, provider, "7027")

    assert binding.provider_id == "7027"

    def load(session: Session) -> tuple[Zaisan, Zaisan]:
        original = session.get(Zaisan, original_id)
        reboot = session.get(Zaisan, reboot_id)
        assert original is not None
        assert reboot is not None
        return original, reboot

    original, reboot = database.run_transaction(load)
    assert (original.title, original.sort_title, original.release_year) == (
        "Ghostbusters",
        "Ghostbusters",
        1984,
    )
    assert (reboot.title, reboot.sort_title, reboot.release_year) == (
        "Ghostbusters",
        "Ghostbusters",
        2016,
    )


async def test_metadata_match_rejects_a_release_year_collision_when_sort_title_is_locked(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    def create(session: Session) -> int:
        root = create_library_root(
            session,
            path=tmp_path / "Movies",
            expected_media_kind=ZaisanKind.MOVIE,
        )
        create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="Ghostbusters",
            release_year=1984,
        )
        return create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="Ghostbusters",
            release_year=2016,
            locked_metadata_fields=frozenset({MetadataField.SORT_TITLE}),
        ).id

    reboot_id = database.run_transaction(create)
    provider = _FakeProvider(
        (),
        {"7027": _movie_details("7027", "Ghostbusters", year=1984)},
    )

    with pytest.raises(MetadataIdentityConflictError, match="conflicts with library item"):
        await _workflow(database, tmp_path / "cache").match_item(reboot_id, provider, "7027")


async def test_metadata_refresh_rejects_a_release_year_collision_when_sort_title_is_locked(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    def create(session: Session) -> int:
        root = create_library_root(
            session,
            path=tmp_path / "Movies",
            expected_media_kind=ZaisanKind.MOVIE,
        )
        create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="Ghostbusters",
            release_year=1984,
        )
        return create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="Ghostbusters",
            release_year=2016,
            locked_metadata_fields=frozenset({MetadataField.SORT_TITLE}),
        ).id

    reboot_id = database.run_transaction(create)
    provider = _FakeProvider(
        (),
        {"7027": _movie_details("7027", "Ghostbusters", year=2016)},
    )
    workflow = _workflow(database, tmp_path / "cache")
    await workflow.match_item(reboot_id, provider, "7027")
    provider.details["7027"] = _movie_details("7027", "Ghostbusters", year=1984)

    with pytest.raises(MetadataIdentityConflictError, match="conflicts with library item"):
        await workflow.refresh_item(reboot_id, (provider,))


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


def _still_reference(revision: str = "/still-v1.png") -> ArtworkReference:
    return ArtworkReference(
        provider="fake",
        kind=ArtworkKind.STILL,
        raw_path=revision,
        source_url=AnyHttpUrl(f"https://images.example.test{revision}"),
    )


def _episode_details(
    series_provider_id: str,
    episode_provider_id: str,
    season_number: int,
    episode_number: int,
    *,
    still: ArtworkReference | None,
    overview: str | None = None,
) -> EpisodeDetails:
    return EpisodeDetails(
        reference=ProviderReference(provider="fake", raw_id=episode_provider_id),
        series_reference=ProviderReference(provider="fake", raw_id=series_provider_id),
        season_number=season_number,
        episode_number=episode_number,
        title=f"Episode {episode_number}",
        overview=overview,
        still=still,
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
    assert provider.poster_list_calls == 0
    cache_path = tmp_path / "cache" / first[0].cache_path
    assert cache_path.is_file()

    await workflow.unmatch_item(item_id)
    removed_files, removed_bytes = await workflow.prune_artwork()

    assert (removed_files, removed_bytes) == (1, len(artwork_content.content))
    assert not cache_path.exists()
    assert database.run_transaction(lambda session: session.scalar(select(CachedArtwork))) is None


async def test_artwork_cache_fetches_season_posters_from_a_matched_series(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    def create() -> tuple[int, int, int, int]:
        def create_items(session: Session) -> tuple[int, int, int, int]:
            root = create_library_root(
                session,
                path=tmp_path / "Shows",
                expected_media_kind=ZaisanKind.SERIES,
            )
            series = create_library_item(
                session,
                library_root_id=root.id,
                item_kind=ZaisanKind.SERIES,
                title="Example Show",
            )
            first_season = create_library_item(
                session,
                library_root_id=root.id,
                parent_id=series.id,
                item_kind=ZaisanKind.SEASON,
                title="Season 1",
                season_number=1,
            )
            second_season = create_library_item(
                session,
                library_root_id=root.id,
                parent_id=series.id,
                item_kind=ZaisanKind.SEASON,
                title="Season 2",
                season_number=2,
            )
            return root.id, series.id, first_season.id, second_season.id

        return database.run_transaction(create_items)

    root_id, series_id, first_season_id, second_season_id = create()
    first_poster = _poster_reference("/season-one.png")
    second_poster = _poster_reference("/season-two.png")
    fanart_poster = ArtworkReference(
        provider="fanart",
        kind=ArtworkKind.POSTER,
        raw_path="fanart-season-one",
        source_url=AnyHttpUrl("https://fanart.example.test/season-one.png"),
        language="en",
        width=1000,
        height=1426,
        vote_count=20,
    )
    image = b"\x89PNG\r\n\x1a\nminimal"
    metadata_provider = _FakeProvider(
        (),
        {
            "series-1": _series_details(
                "series-1",
                "Example Show",
                external_ids=(ExternalIdentifier(namespace="tvdb", value="81189"),),
            )
        },
        ArtworkContent(reference=first_poster, content=image, media_type="image/png"),
        season_details={
            ("series-1", 1): _season_details("series-1", "season-1", 1, poster=first_poster),
            ("series-1", 2): _season_details("series-1", "season-2", 2, poster=second_poster),
        },
    )
    fanart_provider = _SupplementalPosterProvider(
        ArtworkContent(reference=fanart_poster, content=image, media_type="image/png"),
        (fanart_poster,),
        provider_id="tvdb:81189:season:1",
    )
    workflow = _workflow(database, tmp_path / "cache")

    await workflow.match_item(series_id, metadata_provider, "series-1")
    root_cached = await workflow.fetch_posters((metadata_provider,), root_id=root_id)

    assert {record.library_item_id for record in root_cached} == {
        first_season_id,
        second_season_id,
    }
    assert metadata_provider.season_calls == [
        (ProviderReference(provider="fake", raw_id="series-1"), 1),
        (ProviderReference(provider="fake", raw_id="series-1"), 2),
    ]

    selected_cached = await workflow.fetch_posters(
        (metadata_provider, fanart_provider), item_id=first_season_id, include_variants=True
    )

    assert len(selected_cached) == 2
    assert metadata_provider.poster_list_calls == 0
    assert fanart_provider.lookups == [
        PosterLookup(
            reference=ProviderReference(provider="fake", raw_id="season-1"),
            media_kind=ProviderMediaKind.SEASON,
            external_ids=(
                ExternalIdentifier(namespace="fake", value="series-1"),
                ExternalIdentifier(namespace="tvdb", value="81189"),
            ),
            season_number=1,
        )
    ]

    fresh_first_poster = _poster_reference("/season-one-fresh.png")
    metadata_provider.season_details[("series-1", 1)] = _season_details(
        "series-1", "season-1", 1, poster=fresh_first_poster
    )
    await workflow.fetch_posters((metadata_provider,), item_id=first_season_id)

    def records(session: Session) -> tuple[CachedArtwork, ...]:
        return tuple(session.scalars(select(CachedArtwork).order_by(CachedArtwork.id)))

    cached_records = database.run_transaction(records)
    assert sorted(
        (
            record.library_item_id,
            record.provider,
            record.provider_id,
            record.owner_provider,
            record.owner_provider_id,
        )
        for record in cached_records
    ) == sorted(
        (
            (first_season_id, "fake", "season-1", "fake", "series-1"),
            (second_season_id, "fake", "season-2", "fake", "series-1"),
            (first_season_id, "fanart", "tvdb:81189:season:1", "fake", "series-1"),
        )
    )
    assert {
        (record.library_item_id, record.provider, record.provider_revision)
        for record in cached_records
    } == {
        (first_season_id, "fake", fresh_first_poster.raw_path),
        (second_season_id, "fake", second_poster.raw_path),
        (first_season_id, "fanart", fanart_poster.raw_path),
    }

    metadata_provider.missing_seasons.add(("series-1", 1))
    assert await workflow.fetch_posters((metadata_provider,), item_id=first_season_id) == ()
    cached_records = database.run_transaction(records)
    assert {
        (record.library_item_id, record.provider, record.provider_revision)
        for record in cached_records
    } == {
        (first_season_id, "fake", fresh_first_poster.raw_path),
        (second_season_id, "fake", second_poster.raw_path),
        (first_season_id, "fanart", fanart_poster.raw_path),
    }

    metadata_provider.missing_seasons.clear()
    metadata_provider.season_details[("series-1", 1)] = _season_details(
        "series-1", "season-1", 1, poster=None
    )
    await workflow.fetch_posters((metadata_provider,), item_id=first_season_id)

    cached_records = database.run_transaction(records)
    assert {
        (record.library_item_id, record.provider, record.provider_revision)
        for record in cached_records
    } == {
        (second_season_id, "fake", second_poster.raw_path),
        (first_season_id, "fanart", fanart_poster.raw_path),
    }
    assert await workflow.prune_artwork() == (0, 0)

    await workflow.unmatch_item(series_id)

    assert await workflow.prune_artwork() == (2, len(image) * 2)


async def test_artwork_cache_fetches_episode_stills_from_a_matched_series(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    def create() -> tuple[int, int, int, int, int]:
        def create_items(session: Session) -> tuple[int, int, int, int, int]:
            root = create_library_root(
                session,
                path=tmp_path / "Shows",
                expected_media_kind=ZaisanKind.SERIES,
            )
            series = create_library_item(
                session,
                library_root_id=root.id,
                item_kind=ZaisanKind.SERIES,
                title="Example Show",
            )
            season = create_library_item(
                session,
                library_root_id=root.id,
                parent_id=series.id,
                item_kind=ZaisanKind.SEASON,
                title="Season 1",
                season_number=1,
            )
            first_episode = create_library_item(
                session,
                library_root_id=root.id,
                parent_id=season.id,
                item_kind=ZaisanKind.EPISODE,
                title="Episode 1",
                season_number=1,
                episode_number=1,
            )
            second_episode = create_library_item(
                session,
                library_root_id=root.id,
                parent_id=season.id,
                item_kind=ZaisanKind.EPISODE,
                title="Episode 2",
                season_number=1,
                episode_number=2,
                overview="Keep this local description.",
                locked_metadata_fields=frozenset((MetadataField.OVERVIEW,)),
            )
            return root.id, series.id, season.id, first_episode.id, second_episode.id

        return database.run_transaction(create_items)

    root_id, series_id, season_id, first_episode_id, second_episode_id = create()
    first_still = _still_reference("/episode-one.png")
    second_still = _still_reference("/episode-two.png")
    image = b"\x89PNG\r\n\x1a\nminimal"
    provider = _FakeProvider(
        (),
        {"series-1": _series_details("series-1", "Example Show")},
        ArtworkContent(reference=first_still, content=image, media_type="image/png"),
        season_details={
            ("series-1", 1): _season_details(
                "series-1",
                "season-1",
                1,
                poster=None,
                episodes=(
                    _episode_details(
                        "series-1",
                        "episode-1",
                        1,
                        1,
                        still=first_still,
                        overview="A strange signal reaches Earth.",
                    ),
                    _episode_details(
                        "series-1",
                        "episode-2",
                        1,
                        2,
                        still=second_still,
                        overview="The crew prepares for departure.",
                    ),
                ),
            )
        },
    )
    workflow = _workflow(database, tmp_path / "cache")

    await workflow.match_item(series_id, provider, "series-1")
    cached = await workflow.fetch_posters((provider,), root_id=root_id)

    assert {(record.library_item_id, record.kind) for record in cached} == {
        (first_episode_id, CachedArtworkKind.STILL),
        (second_episode_id, CachedArtworkKind.STILL),
    }
    assert provider.season_calls == [(ProviderReference(provider="fake", raw_id="series-1"), 1)]
    assert provider.artwork_calls == 2

    def episode_overviews(session: Session) -> dict[int, str | None]:
        return {
            episode.id: episode.overview
            for episode in session.scalars(
                select(Zaisan).where(Zaisan.id.in_((first_episode_id, second_episode_id)))
            )
        }

    assert database.run_transaction(episode_overviews) == {
        first_episode_id: "A strange signal reaches Earth.",
        second_episode_id: "Keep this local description.",
    }

    selected = await workflow.fetch_posters(
        (provider,), item_id=first_episode_id, include_variants=True
    )

    assert [(record.library_item_id, record.kind) for record in selected] == [
        (first_episode_id, CachedArtworkKind.STILL)
    ]
    assert provider.poster_list_calls == 0

    fresh_still = _still_reference("/episode-one-fresh.png")
    provider.season_details[("series-1", 1)] = _season_details(
        "series-1",
        "season-1",
        1,
        poster=None,
        episodes=(
            _episode_details("series-1", "episode-1", 1, 1, still=fresh_still),
            _episode_details("series-1", "episode-2", 1, 2, still=second_still),
        ),
    )
    await workflow.fetch_posters((provider,), item_id=first_episode_id)

    def records(session: Session) -> tuple[CachedArtwork, ...]:
        return tuple(session.scalars(select(CachedArtwork).order_by(CachedArtwork.id)))

    cached_records = database.run_transaction(records)
    assert sorted(
        (
            record.library_item_id,
            record.owner_provider,
            record.owner_provider_id,
            record.artwork_kind,
        )
        for record in cached_records
    ) == sorted(
        (
            (first_episode_id, "fake", "series-1", CachedArtworkKind.STILL),
            (second_episode_id, "fake", "series-1", CachedArtworkKind.STILL),
        )
    )
    assert {(record.library_item_id, record.provider_revision) for record in cached_records} == {
        (first_episode_id, fresh_still.raw_path),
        (second_episode_id, second_still.raw_path),
    }
    assert season_id not in {record.library_item_id for record in cached_records}

    provider.season_details[("series-1", 1)] = _season_details(
        "series-1",
        "season-1",
        1,
        poster=None,
        episodes=(
            _episode_details(
                "series-1",
                "episode-1",
                1,
                1,
                still=None,
                overview="The signal is decoded.",
            ),
            _episode_details("series-1", "episode-2", 1, 2, still=second_still),
        ),
    )
    await workflow.fetch_posters((provider,), item_id=first_episode_id)

    cached_records = database.run_transaction(records)
    assert {(record.library_item_id, record.provider_revision) for record in cached_records} == {
        (second_episode_id, second_still.raw_path)
    }
    assert database.run_transaction(episode_overviews) == {
        first_episode_id: "The signal is decoded.",
        second_episode_id: "Keep this local description.",
    }

    await workflow.unmatch_item(series_id)

    assert await workflow.prune_artwork() == (1, len(image))


async def test_artwork_cache_fetches_ordered_poster_variants_for_one_shared_picker(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    item_id = _create_movie(database, tmp_path / "Movies", title="Paprika", year=2006)
    primary = _poster_reference("/primary.png")
    obsolete = _poster_reference("/obsolete.png")
    fresh = _poster_reference("/fresh.png")
    japanese = ArtworkReference(
        provider="fake",
        kind=ArtworkKind.POSTER,
        raw_path="/japanese.png",
        source_url=AnyHttpUrl("https://images.example.test/japanese.png"),
        language="ja",
        width=1000,
        height=1500,
        vote_average=8.7,
        vote_count=50,
    )
    english_primary = ArtworkReference(
        provider="fake",
        kind=ArtworkKind.POSTER,
        raw_path="/primary.png",
        source_url=AnyHttpUrl("https://images.example.test/primary.png"),
        language="en",
        width=2000,
        height=3000,
        vote_average=8.1,
        vote_count=200,
    )
    artwork_content = ArtworkContent(
        reference=primary,
        content=b"\x89PNG\r\n\x1a\nminimal",
        media_type="image/png",
    )
    provider = _FakeProvider(
        (_search_result("51", "Paprika", year=2006, poster=primary),),
        {"51": _movie_details("51", "Paprika", year=2006, poster=primary)},
        artwork_content,
        posters=(obsolete, japanese, english_primary),
    )
    workflow = _workflow(database, tmp_path / "cache")

    await workflow.search_item(item_id, (provider,))
    cached = await workflow.fetch_posters((provider,), item_id=item_id, include_variants=True)

    assert len(cached) == 3
    assert provider.poster_list_calls == 1
    assert provider.artwork_calls == 3

    def records(session: Session) -> tuple[CachedArtwork, ...]:
        return tuple(
            session.scalars(
                select(CachedArtwork)
                .where(CachedArtwork.library_item_id == item_id)
                .order_by(CachedArtwork.display_order)
            )
        )

    primary_record, obsolete_record, japanese_record = database.run_transaction(records)
    assert primary_record.is_primary is True
    assert primary_record.display_order == 0
    assert (primary_record.language, primary_record.width, primary_record.height) == (
        "en",
        2000,
        3000,
    )
    assert (primary_record.vote_average, primary_record.vote_count) == (8.1, 200)
    assert japanese_record.is_primary is False
    assert japanese_record.display_order == 2
    assert japanese_record.language == "ja"

    def select_japanese(session: Session) -> None:
        item = session.get(Zaisan, item_id)
        assert item is not None
        item.selected_artwork_ids = {"poster": japanese_record.id}

    database.run_transaction(select_japanese)
    obsolete_path = tmp_path / "cache" / obsolete_record.cache_relative_path
    provider.posters = (english_primary, fresh)

    await workflow.fetch_posters((provider,), item_id=item_id, include_variants=True)
    await workflow.fetch_posters((provider,), item_id=item_id)

    assert provider.poster_list_calls == 2
    assert provider.artwork_calls == 4
    records_by_revision = {
        record.provider_revision: record for record in database.run_transaction(records)
    }
    assert set(records_by_revision) == {"/primary.png", "/japanese.png", "/fresh.png"}
    assert records_by_revision["/japanese.png"].id == japanese_record.id
    assert not obsolete_path.exists()


async def test_artwork_cache_limits_concurrent_poster_variant_lookups(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    first_poster = _poster_reference("/first.png")
    second_poster = _poster_reference("/second.png")

    def create(session: Session) -> tuple[int, int, int]:
        root = create_library_root(
            session,
            path=tmp_path / "Movies",
            expected_media_kind=ZaisanKind.MOVIE,
        )
        first = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="First",
            release_year=2001,
        )
        second = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="Second",
            release_year=2002,
        )
        return root.id, first.id, second.id

    root_id, first_id, second_id = database.run_transaction(create)
    provider = _ConcurrencyTrackingPosterProvider(
        (),
        {
            "first": _movie_details("first", "First", year=2001, poster=first_poster),
            "second": _movie_details("second", "Second", year=2002, poster=second_poster),
        },
        ArtworkContent(
            reference=first_poster,
            content=b"\x89PNG\r\n\x1a\nminimal",
            media_type="image/png",
        ),
    )
    workflow = _workflow(database, tmp_path / "cache", artwork_concurrency=1)

    await workflow.match_item(first_id, provider, "first")
    await workflow.match_item(second_id, provider, "second")
    await workflow.fetch_posters((provider,), root_id=root_id, include_variants=True)

    assert provider.poster_list_calls == 2
    assert provider.maximum_poster_lookups == 1


async def test_artwork_cache_interleaves_supplemental_poster_sources_and_prunes_them_by_owner(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    item_id = _create_movie(database, tmp_path / "Movies", title="Paprika", year=2006)
    primary = _poster_reference("/primary.png")
    tmdb_alternative = _poster_reference("/tmdb-alternative.png")
    replacement = _poster_reference("/replacement.png")
    fanart_first = ArtworkReference(
        provider="fanart",
        kind=ArtworkKind.POSTER,
        raw_path="fanart-first",
        source_url=AnyHttpUrl("https://fanart.example.test/first.png"),
        language="en",
        width=2000,
        height=3000,
        vote_count=25,
    )
    fanart_second = ArtworkReference(
        provider="fanart",
        kind=ArtworkKind.POSTER,
        raw_path="fanart-second",
        source_url=AnyHttpUrl("https://fanart.example.test/second.png"),
        language="ja",
        width=1000,
        height=1500,
        vote_count=10,
    )
    image = b"\x89PNG\r\n\x1a\nminimal"
    metadata_provider = _FakeProvider(
        (_search_result("51", "Paprika", year=2006, poster=primary),),
        {
            "51": _movie_details(
                "51",
                "Paprika",
                year=2006,
                poster=primary,
                external_ids=(ExternalIdentifier(namespace="tmdb", value="51"),),
            ),
            "52": _movie_details(
                "52",
                "Paprika",
                year=2006,
                poster=replacement,
                external_ids=(ExternalIdentifier(namespace="tmdb", value="52"),),
            ),
        },
        ArtworkContent(reference=primary, content=image, media_type="image/png"),
        posters=(tmdb_alternative,),
    )
    fanart_provider = _SupplementalPosterProvider(
        ArtworkContent(reference=fanart_first, content=image, media_type="image/png"),
        (fanart_first, fanart_second),
        provider_id="tmdb:51",
    )
    workflow = _workflow(database, tmp_path / "cache")

    await workflow.search_item(item_id, (metadata_provider,))
    cached = await workflow.fetch_posters(
        (metadata_provider, fanart_provider), item_id=item_id, include_variants=True
    )

    assert len(cached) == 4
    assert metadata_provider.artwork_calls == 2
    assert fanart_provider.artwork_calls == 2
    assert fanart_provider.poster_list_calls == 1
    assert fanart_provider.lookups == [
        PosterLookup(
            reference=ProviderReference(provider="fake", raw_id="51"),
            media_kind=ProviderMediaKind.MOVIE,
            external_ids=(
                ExternalIdentifier(namespace="fake", value="51"),
                ExternalIdentifier(namespace="tmdb", value="51"),
            ),
        )
    ]

    def records(session: Session) -> tuple[CachedArtwork, ...]:
        return tuple(
            session.scalars(
                select(CachedArtwork)
                .where(CachedArtwork.library_item_id == item_id)
                .order_by(CachedArtwork.display_order)
            )
        )

    cached_records = database.run_transaction(records)
    assert [
        (
            record.provider,
            record.provider_id,
            record.owner_provider,
            record.owner_provider_id,
            record.provider_revision,
            record.is_primary,
        )
        for record in cached_records
    ] == [
        ("fake", "51", "fake", "51", "/primary.png", True),
        ("fake", "51", "fake", "51", "/tmdb-alternative.png", False),
        ("fanart", "tmdb:51", "fake", "51", "fanart-first", False),
        ("fanart", "tmdb:51", "fake", "51", "fanart-second", False),
    ]
    assert await workflow.prune_artwork() == (0, 0)

    await workflow.match_item(item_id, metadata_provider, "52")

    assert await workflow.prune_artwork() == (4, len(image) * 4)


@pytest.mark.parametrize(
    ("unavailable_provider", "failure_operation", "expected_providers"),
    (
        ("fake", "download", frozenset({"fanart"})),
        ("fanart", "download", frozenset({"fake"})),
        ("fake", "poster lookup", frozenset({"fake", "fanart"})),
        ("fanart", "poster lookup", frozenset({"fake"})),
    ),
)
async def test_artwork_cache_uses_artwork_from_reachable_providers(
    database: KatalogDatabase,
    tmp_path: Path,
    unavailable_provider: Literal["fake", "fanart"],
    failure_operation: Literal["download", "poster lookup"],
    expected_providers: frozenset[str],
) -> None:
    item_id = _create_movie(database, tmp_path / "Movies", title="Paprika", year=2006)
    primary = _poster_reference("/primary.png")
    supplemental = ArtworkReference(
        provider="fanart",
        kind=ArtworkKind.POSTER,
        raw_path="fanart-primary",
        source_url=AnyHttpUrl("https://fanart.example.test/primary.png"),
    )
    image = b"\x89PNG\r\n\x1a\nminimal"
    failure = KourierError(
        ProviderErrorCategory.TRANSIENT,
        f"{unavailable_provider} is unavailable.",
        provider=unavailable_provider,
    )
    primary_artwork_error = (
        failure if (unavailable_provider, failure_operation) == ("fake", "download") else None
    )
    primary_listing_error = (
        failure if (unavailable_provider, failure_operation) == ("fake", "poster lookup") else None
    )
    supplemental_artwork_error = (
        failure if (unavailable_provider, failure_operation) == ("fanart", "download") else None
    )
    supplemental_listing_error = (
        failure
        if (unavailable_provider, failure_operation) == ("fanart", "poster lookup")
        else None
    )
    metadata_provider = _FakeProvider(
        (_search_result("51", "Paprika", year=2006, poster=primary),),
        {"51": _movie_details("51", "Paprika", year=2006, poster=primary)},
        ArtworkContent(reference=primary, content=image, media_type="image/png"),
        artwork_error=primary_artwork_error,
        poster_listing_error=primary_listing_error,
    )
    fanart_provider = _SupplementalPosterProvider(
        ArtworkContent(reference=supplemental, content=image, media_type="image/png"),
        (supplemental,),
        provider_id="tmdb:51",
        artwork_error=supplemental_artwork_error,
        poster_listing_error=supplemental_listing_error,
    )
    workflow = _workflow(database, tmp_path / "cache")

    await workflow.search_item(item_id, (metadata_provider,))
    cached = await workflow.fetch_posters(
        (metadata_provider, fanart_provider), item_id=item_id, include_variants=True
    )

    expected_fanart_artwork_calls = (
        0 if (unavailable_provider, failure_operation) == ("fanart", "poster lookup") else 1
    )
    assert {artwork.provider for artwork in cached} == expected_providers
    assert metadata_provider.artwork_calls == 1
    assert fanart_provider.artwork_calls == expected_fanart_artwork_calls


async def test_artwork_cache_fetches_only_the_requested_item(
    database: KatalogDatabase, tmp_path: Path
) -> None:
    first_item_id = _create_movie(database, tmp_path / "First", title="Paprika", year=2006)
    second_item_id = _create_movie(database, tmp_path / "Second", title="Perfect Blue", year=1997)
    first_poster = _poster_reference("/paprika.png")
    second_poster = _poster_reference("/perfect-blue.png")
    content = ArtworkContent(
        reference=first_poster,
        content=b"\x89PNG\r\n\x1a\nminimal",
        media_type="image/png",
    )
    provider = _FakeProvider(
        (
            _search_result("51", "Paprika", year=2006, poster=first_poster),
            _search_result("61", "Perfect Blue", year=1997, poster=second_poster),
        ),
        {
            "51": _movie_details("51", "Paprika", year=2006, poster=first_poster),
            "61": _movie_details("61", "Perfect Blue", year=1997, poster=second_poster),
        },
        content,
    )
    workflow = _workflow(database, tmp_path / "cache")

    await workflow.search_item(first_item_id, (provider,))
    await workflow.search_item(second_item_id, (provider,))
    artwork = await workflow.fetch_posters((provider,), item_id=first_item_id)

    assert len(artwork) == provider.artwork_calls == 1
    assert artwork[0].library_item_id == first_item_id
    cached_item_ids = database.run_transaction(
        lambda session: tuple(session.scalars(select(CachedArtwork.library_item_id)))
    )
    assert cached_item_ids == (first_item_id,)
    with pytest.raises(ValueError, match="either a library root or an item"):
        await workflow.fetch_posters((provider,), root_id=1, item_id=first_item_id)


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

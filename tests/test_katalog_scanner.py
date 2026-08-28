from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from kasana.katalog.cli import app as katalog_cli
from kasana.katalog.container import canonical_container
from kasana.katalog.database import KatalogDatabase
from kasana.katalog.models import (
    AuditCategory,
    AuditIssue,
    AvailabilityState,
    Kura,
    MediaFile,
    MetadataBinding,
    MetadataMatchStatus,
    Zaisan,
    ZaisanKind,
)
from kasana.katalog.numerals import natural_sort_key
from kasana.katalog.parsing import (
    LibraryLayout,
    ParsedMediaKind,
    ParseFailure,
    is_decade_directory,
    parse_episode_numbers,
    parse_episode_range,
    parse_media_path,
    parse_season_number,
)
from kasana.katalog.probe import FFProbeClient, ProbeFailure, ProbeResult
from kasana.katalog.scanning import IncrementalScanner, ScanCancelledError
from kasana.katalog.scanning.discovery import discover, probe_audit_findings, sidecar_matches_video
from kasana.katalog.services import attach_media_file, create_library_item, create_library_root
from kasana.katalog.settings import KatalogSettings


class _FakeFfprobeClient(FFProbeClient):
    def __init__(self, executable: str, result: ProbeResult) -> None:
        super().__init__(executable)
        self.result = result
        self.calls: list[tuple[Path, ...]] = []

    async def probe_many(
        self, paths: Sequence[Path], *, concurrency: int
    ) -> tuple[dict[Path, ProbeResult], tuple[ProbeFailure, ...]]:
        assert concurrency == 4
        requested_paths = tuple(paths)
        self.calls.append(requested_paths)
        return {path: self.result for path in requested_paths}, ()


class _FakeProcess:
    def __init__(self, stdout: bytes) -> None:
        self.returncode = 0
        self._stdout = stdout

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, b""


class _FailingFfprobeClient(FFProbeClient):
    async def probe_many(
        self, paths: Sequence[Path], *, concurrency: int
    ) -> tuple[dict[Path, ProbeResult], tuple[ProbeFailure, ...]]:
        assert concurrency == 4
        return {}, tuple(ProbeFailure(path, "Invalid media data.") for path in paths)


@pytest.fixture
def fake_ffprobe(tmp_path: Path) -> Path:
    executable = tmp_path / "ffprobe"
    executable.write_text("#!/bin/sh\nprintf '{}\\n'\n", encoding="utf-8")
    executable.chmod(0o755)
    return executable


def _probe_result(*, container: str = "matroska,webm", codec: str = "h264") -> ProbeResult:
    return ProbeResult(
        container=container,
        duration_seconds=1_234.5,
        video_streams=({"codec": codec, "width": 1920, "height": 1080, "frame_rate": 24.0},),
        audio_streams=(
            {"codec": "aac", "language": "eng", "channels": 2, "channel_layout": "stereo"},
        ),
        subtitle_streams=(
            {"codec": "subrip", "language": "eng", "default": True, "forced": False},
        ),
    )


def _register_root(database: KatalogDatabase, path: Path, kind: ZaisanKind) -> None:
    database.run_transaction(
        lambda session: create_library_root(
            session,
            path=path,
            expected_media_kind=kind,
        )
    )


def _scanner(
    database: KatalogDatabase, fake_ffprobe: Path, result: ProbeResult
) -> _FakeFfprobeClient:
    scanner = IncrementalScanner(
        database,
        video_extensions=frozenset({".mkv", ".mp4", ".webm"}),
        probe_concurrency=4,
        ffprobe_executable=str(fake_ffprobe),
    )
    fake_client = _FakeFfprobeClient(str(fake_ffprobe), result)
    scanner.prober = fake_client
    return fake_client


def _run_scan(database: KatalogDatabase, fake_client: _FakeFfprobeClient) -> IncrementalScanner:
    scanner = IncrementalScanner(
        database,
        video_extensions=frozenset({".mkv", ".mp4", ".webm"}),
        probe_concurrency=4,
        ffprobe_executable=fake_client.executable,
    )
    scanner.prober = fake_client
    return scanner


def test_discovery_stops_at_a_cooperative_cancellation_checkpoint(tmp_path: Path) -> None:
    root = tmp_path / "Movies"
    root.mkdir()
    (root / "Stargate.mkv").write_bytes(b"media")

    with pytest.raises(ScanCancelledError, match="cancellation was requested"):
        discover(root, frozenset({".mkv"}), cancellation_requested=lambda: True)


def test_incremental_scan_detects_add_change_move_and_missing(
    database: KatalogDatabase, fake_ffprobe: Path, tmp_path: Path
) -> None:
    movies = tmp_path / "Movies"
    film = movies / "1990s" / "Stargate.mkv"
    film.parent.mkdir(parents=True)
    film.write_bytes(b"first")
    _register_root(database, movies, ZaisanKind.MOVIE)
    fake_client = _scanner(database, fake_ffprobe, _probe_result())
    scanner = _run_scan(database, fake_client)

    first = scanner.scan()
    assert first.totals.discovered == 1
    assert first.totals.added == 1
    assert len(fake_client.calls) == 1

    def read_file(session: Session) -> tuple[MediaFile, Zaisan]:
        media_file = session.scalar(select(MediaFile))
        item = session.scalar(select(Zaisan))
        assert media_file is not None
        assert item is not None
        return media_file, item

    media_file, item = database.run_transaction(read_file)
    assert item.item_kind is ZaisanKind.MOVIE
    assert item.title == "Stargate"
    assert media_file.container == "matroska"
    assert media_file.subtitle_streams[0]["default"] is True

    unchanged = scanner.scan()
    assert unchanged.totals.unchanged == 1
    assert len(fake_client.calls) == 1

    film.write_bytes(b"changed file")
    changed = scanner.scan()
    assert changed.totals.changed == 1
    assert len(fake_client.calls) == 2

    renamed = film.with_name("Stargate Remastered.mkv")
    film.rename(renamed)
    moved = scanner.scan()
    assert moved.totals.moved == 1
    assert len(fake_client.calls) == 2

    renamed.unlink()
    unavailable = scanner.scan()
    assert unavailable.totals.unavailable == 1

    def read_availability(session: Session) -> AvailabilityState:
        persisted_file = session.scalar(select(MediaFile))
        assert persisted_file is not None
        return persisted_file.availability

    assert database.run_transaction(read_availability) is AvailabilityState.UNAVAILABLE


def test_scan_handles_missing_library_root_and_recovers(
    database: KatalogDatabase, fake_ffprobe: Path, tmp_path: Path
) -> None:
    movies = tmp_path / "Movies"
    film = movies / "1990s" / "Stargate.mkv"
    film.parent.mkdir(parents=True)
    film.write_bytes(b"first")
    _register_root(database, movies, ZaisanKind.MOVIE)
    fake_client = _scanner(database, fake_ffprobe, _probe_result())
    scanner = _run_scan(database, fake_client)

    first = scanner.scan()
    assert first.totals.added == 1

    film.unlink()
    film.parent.rmdir()
    movies.rmdir()
    missing = scanner.scan()

    assert missing.totals.failed == 1
    assert missing.totals.unavailable == 1
    assert missing.findings[0].path == movies
    assert "library root" in missing.findings[0].message
    assert len(fake_client.calls) == 1

    def unavailable_state(session: Session) -> tuple[AvailabilityState, tuple[str, ...]]:
        persisted_file = session.scalar(select(MediaFile))
        assert persisted_file is not None
        issues = session.scalars(select(AuditIssue).order_by(AuditIssue.id)).all()
        return persisted_file.availability, tuple(issue.message for issue in issues)

    availability, issue_messages = database.run_transaction(unavailable_state)
    assert availability is AvailabilityState.UNAVAILABLE
    assert issue_messages == ("The configured library root is not an accessible directory.",)

    film.parent.mkdir(parents=True)
    film.write_bytes(b"first")
    recovered = scanner.scan()

    assert recovered.totals.failed == 0
    assert recovered.findings == ()

    def recovered_state(session: Session) -> tuple[AvailabilityState, int]:
        persisted_file = session.scalar(select(MediaFile))
        assert persisted_file is not None
        issue_count = session.scalar(select(func.count()).select_from(AuditIssue))
        return persisted_file.availability, issue_count or 0

    recovered_availability, issue_count = database.run_transaction(recovered_state)
    assert recovered_availability is AvailabilityState.AVAILABLE
    assert issue_count == 0


def test_real_library_layouts_never_materialise_organisational_folders(
    database: KatalogDatabase, fake_ffprobe: Path, tmp_path: Path
) -> None:
    movies = tmp_path / "Movies"
    shows = tmp_path / "TVShows"
    anime = tmp_path / "Anime"
    for path in (
        movies / "00's" / "Film Name.mkv",
        movies / "10's" / "Another Film" / "Another Film.mkv",
        movies / "10's" / "Another Film" / "extra.mkv",
        movies / "10's" / "Another Film" / "poster.jpg",
        movies / "10's" / "Another Film" / "Another Film.en.srt",
        movies / "1990s" / "Nineteen Ninety.mkv",
        movies / "2000s" / "Two Thousand.mkv",
        shows / "Show Name" / "Season 01" / "S01E01.mkv",
        anime / "Shows" / "Anime Name" / "Volume 01" / "E01.mkv",
        anime / "Films" / "Anime Film.mkv",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"video")
    _register_root(database, movies, ZaisanKind.MOVIE)
    _register_root(database, shows, ZaisanKind.SERIES)
    _register_root(database, anime, ZaisanKind.SERIES)

    scanner = _run_scan(database, _scanner(database, fake_ffprobe, _probe_result()))

    result = scanner.scan()

    assert result.totals.added == 8

    def hierarchy(session: Session) -> list[tuple[str, ZaisanKind, str | None]]:
        items = session.scalars(select(Zaisan).order_by(Zaisan.id)).all()
        by_id = {item.id: item for item in items}
        return [
            (item.title, item.item_kind, by_id[item.parent_id].title if item.parent_id else None)
            for item in items
        ]

    indexed = database.run_transaction(hierarchy)
    assert ("Film Name", ZaisanKind.MOVIE, None) in indexed
    assert ("Another Film", ZaisanKind.MOVIE, None) in indexed
    assert ("extra", ZaisanKind.EXTRA, "Another Film") in indexed
    assert ("Show Name", ZaisanKind.SERIES, None) in indexed
    assert ("Season 1", ZaisanKind.SEASON, "Show Name") in indexed
    assert ("S01E01", ZaisanKind.EPISODE, "Season 1") in indexed
    assert ("Anime Name", ZaisanKind.SERIES, None) in indexed
    assert ("Anime Film", ZaisanKind.MOVIE, None) in indexed
    organisational_titles = {
        "00's",
        "10's",
        "1990s",
        "2000s",
        "Movies",
        "TVShows",
        "Anime",
        "Shows",
        "Films",
        "Volume 01",
    }
    assert not organisational_titles & {title for title, _, _ in indexed}

    def local_sidecars(session: Session) -> tuple[str | None, list[str]]:
        media_file = session.scalar(
            select(MediaFile).where(
                MediaFile.absolute_path
                == str(movies / "10's" / "Another Film" / "Another Film.mkv")
            )
        )
        assert media_file is not None
        return media_file.local_poster_path, media_file.subtitle_sidecar_paths

    poster_path, subtitle_paths = database.run_transaction(local_sidecars)
    assert poster_path == str(movies / "10's" / "Another Film" / "poster.jpg")
    assert subtitle_paths == [str(movies / "10's" / "Another Film" / "Another Film.en.srt")]


def test_movie_directory_with_multiple_main_candidates_requires_review(
    database: KatalogDatabase, fake_ffprobe: Path, tmp_path: Path
) -> None:
    movies = tmp_path / "Movies"
    folder = movies / "10's" / "Ambiguous Film"
    folder.mkdir(parents=True)
    (folder / "Feature one.mkv").write_bytes(b"one")
    (folder / "Feature two.mkv").write_bytes(b"two")
    _register_root(database, movies, ZaisanKind.MOVIE)

    result = _run_scan(database, _scanner(database, fake_ffprobe, _probe_result())).scan()

    assert result.totals.added == 0
    assert any(
        finding.category is AuditCategory.AMBIGUOUS_STRUCTURE
        and "Multiple possible main movie files" in finding.message
        for finding in result.findings
    )


def test_abbreviated_decade_directories_index_files_and_repair_legacy_grouping(
    database: KatalogDatabase, fake_ffprobe: Path, tmp_path: Path
) -> None:
    movies = tmp_path / "Movies"
    film = movies / "00's" / "Cars (2006).mp4"
    film.parent.mkdir(parents=True)
    film.write_bytes(b"video")
    _register_root(database, movies, ZaisanKind.MOVIE)
    stat_result = film.stat()

    def create_legacy_record(session: Session) -> None:
        root = session.scalar(select(Kura))
        assert root is not None
        legacy_item = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="00's",
        )
        attach_media_file(
            session,
            library_item_id=legacy_item.id,
            absolute_path=film,
            size_bytes=stat_result.st_size,
            mtime_ns=stat_result.st_mtime_ns,
            filesystem_device=stat_result.st_dev,
            filesystem_inode=stat_result.st_ino,
            container="mp4",
        )

    database.run_transaction(create_legacy_record)
    fake_client = _scanner(database, fake_ffprobe, _probe_result())
    result = _run_scan(database, fake_client).scan()
    assert result.totals.changed == 1

    def item_details(session: Session) -> tuple[str, int | None]:
        file = session.scalar(select(MediaFile))
        assert file is not None
        return file.library_item.title, file.library_item.release_year

    assert database.run_transaction(item_details) == ("Cars", 2006)


def test_scan_preserves_matched_movie_identity_when_local_filename_differs(
    database: KatalogDatabase, fake_ffprobe: Path, tmp_path: Path
) -> None:
    movies = tmp_path / "Movies"
    film = movies / "2000s" / "Local File Title (2006).mkv"
    film.parent.mkdir(parents=True)
    film.write_bytes(b"video")
    _register_root(database, movies, ZaisanKind.MOVIE)
    stat_result = film.stat()

    def create_matched_record(session: Session) -> int:
        root = session.scalar(select(Kura))
        assert root is not None
        item = create_library_item(
            session,
            library_root_id=root.id,
            item_kind=ZaisanKind.MOVIE,
            title="Canonical Provider Title",
            release_year=2006,
        )
        attach_media_file(
            session,
            library_item_id=item.id,
            absolute_path=film,
            size_bytes=stat_result.st_size,
            mtime_ns=stat_result.st_mtime_ns,
            filesystem_device=stat_result.st_dev,
            filesystem_inode=stat_result.st_ino,
            container="matroska",
        )
        session.add(
            MetadataBinding(
                library_item_id=item.id,
                provider="tmdb",
                provider_id="42",
                provider_media_kind=ZaisanKind.MOVIE,
                status=MetadataMatchStatus.MATCHED,
                scoring_explanation=[],
                provider_external_ids=[],
            )
        )
        return item.id

    item_id = database.run_transaction(create_matched_record)
    result = _run_scan(database, _scanner(database, fake_ffprobe, _probe_result())).scan()

    assert result.totals.unchanged == 1

    def item_title(session: Session) -> tuple[int, str]:
        media_file = session.scalar(select(MediaFile))
        assert media_file is not None
        return media_file.library_item_id, media_file.library_item.title

    assert database.run_transaction(item_title) == (item_id, "Canonical Provider Title")


def test_scan_preserves_matched_series_identity_when_an_episode_file_changes(
    database: KatalogDatabase, fake_ffprobe: Path, tmp_path: Path
) -> None:
    shows = tmp_path / "TVShows"
    episode_path = shows / "Local Folder Title" / "Season 1" / "S01E01.mkv"
    episode_path.parent.mkdir(parents=True)
    episode_path.write_bytes(b"first")
    _register_root(database, shows, ZaisanKind.SERIES)
    fake_client = _scanner(database, fake_ffprobe, _probe_result())
    scanner = _run_scan(database, fake_client)

    first_scan = scanner.scan()
    assert first_scan.totals.added == 1

    def match_series(session: Session) -> int:
        series = session.scalar(select(Zaisan).where(Zaisan.item_kind == ZaisanKind.SERIES))
        assert series is not None
        series.title = "Canonical Provider Title"
        series.sort_title = "Canonical Provider Title"
        session.add(
            MetadataBinding(
                library_item_id=series.id,
                provider="tmdb",
                provider_id="42",
                provider_media_kind=ZaisanKind.SERIES,
                status=MetadataMatchStatus.MATCHED,
                scoring_explanation=[],
                provider_external_ids=[],
            )
        )
        return series.id

    series_id = database.run_transaction(match_series)
    episode_path.write_bytes(b"changed")

    changed_scan = scanner.scan()
    assert changed_scan.totals.changed == 1

    def series_details(session: Session) -> tuple[tuple[int, str], ...]:
        return tuple(
            (item.id, item.title)
            for item in session.scalars(
                select(Zaisan)
                .where(Zaisan.item_kind == ZaisanKind.SERIES)
                .order_by(Zaisan.id)
            ).all()
        )

    assert database.run_transaction(series_details) == ((series_id, "Canonical Provider Title"),)


def test_episode_parsing_uses_season_directory_context() -> None:
    assert parse_season_number("Season 02", allow_volume=False) == 2
    assert parse_season_number("Volume 02", allow_volume=False) is None
    assert parse_season_number("Volume 02", allow_volume=True) == 2
    assert parse_episode_numbers("Show s1e2", season_from_directory=1) == (1, 2)
    assert parse_episode_numbers("Show E02", season_from_directory=1) == (1, 2)
    assert parse_episode_numbers("Show [2x03]", season_from_directory=2) == (2, 3)
    assert parse_episode_numbers("Show (2X03)", season_from_directory=2) == (2, 3)
    assert parse_episode_numbers("Show E02", season_from_directory=None) is None
    assert parse_season_number("Season IV", allow_volume=False) == 4
    assert parse_season_number("第十季", allow_volume=False) == 10
    assert parse_season_number("시즌 십", allow_volume=False) == 10
    assert parse_episode_numbers("Show SⅣE十", season_from_directory=4) == (4, 10)
    assert parse_episode_numbers("Show S一E십", season_from_directory=1) == (1, 10)
    assert parse_episode_numbers("Show 第十二話", season_from_directory=1) == (1, 12)
    assert parse_episode_numbers("Show 제십화", season_from_directory=1) == (1, 10)
    assert natural_sort_key("Episode IX") < natural_sort_key("Episode X")
    assert natural_sort_key("Episode 九") < natural_sort_key("Episode 十")
    compact_range = parse_episode_range("Show S01E01E02", season_from_directory=1)
    hyphenated_range = parse_episode_range("Show S01E01-E02", season_from_directory=1)
    assert compact_range == hyphenated_range
    assert compact_range is not None and compact_range.end is not None
    assert (
        compact_range.start.season_number,
        compact_range.start.episode_number,
        compact_range.end.season_number,
        compact_range.end.episode_number,
    ) == (1, 1, 1, 2)

    parsed = parse_media_path(
        Path("/library/TVShows"),
        LibraryLayout.TV_SHOWS,
        Path("/library/TVShows/Show/Season 1/Show E02.mkv"),
    )
    assert not isinstance(parsed, ParseFailure)
    assert parsed.kind is ParsedMediaKind.EPISODE
    assert parsed.season_number == 1
    assert parsed.episode_number == 2

    alternate = parse_media_path(
        Path("/library/TVShows"),
        LibraryLayout.TV_SHOWS,
        Path("/library/TVShows/Show/Season 2/[2x03] The Test.mkv"),
    )
    assert not isinstance(alternate, ParseFailure)
    assert alternate.season_number == 2
    assert alternate.episode_number == 3
    assert alternate.title == "The Test"

    repeated_series_title = parse_media_path(
        Path("/library/TVShows"),
        LibraryLayout.TV_SHOWS,
        Path("/library/TVShows/The Rookie/Season 7/The Rookie - S07E10 - Chaos Agent.mkv"),
    )
    assert not isinstance(repeated_series_title, ParseFailure)
    assert repeated_series_title.title == "Chaos Agent"

    combined = parse_media_path(
        Path("/library/TVShows"),
        LibraryLayout.TV_SHOWS,
        Path("/library/TVShows/Show/Season 1/Show S01E01-E02.mkv"),
    )
    assert not isinstance(combined, ParseFailure)
    assert (
        combined.season_number,
        combined.episode_number,
        combined.episode_end_season_number,
        combined.episode_end_number,
        combined.title,
    ) == (1, 1, 1, 2, "S01E01-E02")

    combined_with_title = parse_media_path(
        Path("/library/TVShows"),
        LibraryLayout.TV_SHOWS,
        Path("/library/TVShows/Show/Season 1/S01E01 - E02 - Children of the Gods.mkv"),
    )
    assert not isinstance(combined_with_title, ParseFailure)
    assert combined_with_title.title == "S01E01-E02 - Children of the Gods"

    cross_season_combined = parse_media_path(
        Path("/library/TVShows"),
        LibraryLayout.TV_SHOWS,
        Path("/library/TVShows/Show/Season 1/Show S01E10-S02E01.mkv"),
    )
    assert not isinstance(cross_season_combined, ParseFailure)
    assert (
        cross_season_combined.season_number,
        cross_season_combined.episode_number,
        cross_season_combined.episode_end_season_number,
        cross_season_combined.episode_end_number,
        cross_season_combined.title,
    ) == (1, 10, 2, 1, "S01E10-S02E01")

    backwards_combined = parse_media_path(
        Path("/library/TVShows"),
        LibraryLayout.TV_SHOWS,
        Path("/library/TVShows/Show/Season 1/Show S01E02-E01.mkv"),
    )
    assert isinstance(backwards_combined, ParseFailure)
    assert "must end after" in backwards_combined.message

    special = parse_media_path(
        Path("/library/TVShows"),
        LibraryLayout.TV_SHOWS,
        Path("/library/TVShows/Show/Season 0/[0x01] Pilot.mkv"),
    )
    assert not isinstance(special, ParseFailure)
    assert special.kind is ParsedMediaKind.SPECIAL
    assert special.title == "Pilot"

    extra = parse_media_path(
        Path("/library/TVShows"),
        LibraryLayout.TV_SHOWS,
        Path("/library/TVShows/Show/Season 1/Extras/Behind the Scenes.mkv"),
    )
    assert not isinstance(extra, ParseFailure)
    assert extra.kind is ParsedMediaKind.EXTRA
    assert extra.parent_series_title == "Show"

    singular_extra = parse_media_path(
        Path("/library/TVShows"),
        LibraryLayout.TV_SHOWS,
        Path("/library/TVShows/Show/Season 1/Extra/Behind the Scenes.mkv"),
    )
    assert not isinstance(singular_extra, ParseFailure)
    assert singular_extra.kind is ParsedMediaKind.EXTRA
    assert singular_extra.parent_series_title == "Show"


def test_scanner_materialises_combined_episode_files_as_one_playable_entry(
    database: KatalogDatabase, fake_ffprobe: Path, tmp_path: Path
) -> None:
    shows = tmp_path / "TVShows"
    first_season = shows / "Example" / "Season 1"
    first_season.mkdir(parents=True)
    combined = first_season / "Example S01E10-S02E01.mkv"
    combined.write_bytes(b"combined video")
    _register_root(database, shows, ZaisanKind.SERIES)

    result = _run_scan(database, _scanner(database, fake_ffprobe, _probe_result())).scan()

    assert result.totals.added == 1

    def combined_entry(session: Session) -> tuple[Zaisan, MediaFile]:
        episode = session.scalar(select(Zaisan).where(Zaisan.item_kind == ZaisanKind.EPISODE))
        media_file = session.scalar(select(MediaFile))
        assert episode is not None
        assert media_file is not None
        return episode, media_file

    episode, media_file = database.run_transaction(combined_entry)
    assert (
        episode.season_number,
        episode.episode_number,
        episode.episode_end_season_number,
        episode.episode_end_number,
        episode.title,
    ) == (1, 10, 2, 1, "S01E10-S02E01")
    assert media_file.library_item_id == episode.id
    assert len(database.run_transaction(lambda session: session.scalars(select(Zaisan)).all())) == 3


@pytest.mark.parametrize(
    ("directory_name", "expected"),
    [
        ("1990s", True),
        ("1990's", True),
        ("90's", True),
        ("Movies", False),
    ],
)
def test_decade_directory_recognition(directory_name: str, expected: bool) -> None:
    assert is_decade_directory(directory_name) is expected


def test_scanner_classifies_extras_and_season_zero_outside_series_episodes(
    database: KatalogDatabase, fake_ffprobe: Path, tmp_path: Path
) -> None:
    shows = tmp_path / "TVShows" / "Example"
    special = shows / "Season 0" / "[0x01] Pilot.mkv"
    extra = shows / "Season 1" / "Extras" / "Behind the Scenes.mkv"
    special.parent.mkdir(parents=True)
    extra.parent.mkdir(parents=True)
    special.write_bytes(b"special")
    extra.write_bytes(b"extra")
    _register_root(database, shows.parent, ZaisanKind.SERIES)

    scanner = _run_scan(database, _scanner(database, fake_ffprobe, _probe_result()))
    result = scanner.scan()
    assert result.totals.added == 2

    def item_kinds(session: Session) -> set[ZaisanKind]:
        return set(session.scalars(select(Zaisan.item_kind)))

    assert database.run_transaction(item_kinds) == {
        ZaisanKind.SERIES,
        ZaisanKind.SPECIAL,
        ZaisanKind.EXTRA,
    }


def test_language_only_ass_sidecar_requires_exactly_one_video() -> None:
    assert sidecar_matches_video(Path("/library/en.ass"), {"episode"})
    assert sidecar_matches_video(Path("/library/eng.ASS"), {"episode"})
    assert not sidecar_matches_video(Path("/library/en.ass"), {"episode-one", "episode-two"})


def test_sidecar_video_basename_matching_is_case_insensitive() -> None:
    assert sidecar_matches_video(Path("/library/Episode.EN.ass"), {"episode"})
    assert sidecar_matches_video(Path("/library/episode.ass"), {"EPISODE"})


@pytest.mark.parametrize(
    ("format_name", "expected"),
    (
        ("mov,mp4,m4a,3gp,3g2,mj2", "isobmff"),
        ("  3g2, mov, m4a, mp4, mj2, 3gp  ", "isobmff"),
        ("matroska,webm", "matroska"),
        (" webm , matroska ", "matroska"),
        ("avi", "avi"),
    ),
)
def test_ffmpeg_container_aliases_normalise_to_one_family(format_name: str, expected: str) -> None:
    assert canonical_container(format_name) == expected


def test_probe_audits_recognised_legacy_and_unrecognised_formats() -> None:
    recognised = {
        Path("/library/movie.mp4"): _probe_result(container="mov,mp4,m4a,3gp,3g2,mj2", codec="vc1"),
        Path("/library/movie.mov"): _probe_result(
            container="mj2, 3g2, 3gp, m4a, mp4, mov", codec="mpeg2video"
        ),
        Path("/library/movie.mkv"): _probe_result(container="matroska,webm"),
        Path("/library/movie.avi"): _probe_result(container="avi"),
    }
    assert probe_audit_findings(recognised) == ()

    unknown_codec = probe_audit_findings(
        {Path("/library/unknown-codec.mkv"): _probe_result(codec="made_up_video")}
    )
    assert unknown_codec[0].category is AuditCategory.UNSUPPORTED_CODEC
    assert unknown_codec[0].message == "Unrecognised codec 'made_up_video'."

    unknown_container = probe_audit_findings(
        {Path("/library/unknown-container.bin"): _probe_result(container="mystery")}
    )
    assert unknown_container[0].category is AuditCategory.UNSUPPORTED_CONTAINER
    assert unknown_container[0].message == "Unrecognised container 'mystery'."


def test_ambiguous_and_orphaned_files_remain_audit_findings(
    database: KatalogDatabase, fake_ffprobe: Path, tmp_path: Path
) -> None:
    movies = tmp_path / "Movies"
    (movies / "2000s" / "Film" / "unexpected").mkdir(parents=True)
    (movies / "2000s" / "Film" / "unexpected" / "clip.mkv").write_bytes(b"video")
    (movies / "2000s" / "Film" / "extras" / "nested").mkdir(parents=True)
    (movies / "2000s" / "Film" / "extras" / "nested" / "clip.mkv").write_bytes(b"video")
    (movies / "2000s" / "Valid.mkv").write_bytes(b"video")
    (movies / "2000s" / "Film" / "orphan.srt").write_text("1", encoding="utf-8")
    (movies / "2000s" / "PosterOnly").mkdir()
    (movies / "2000s" / "PosterOnly" / "poster.jpg").write_bytes(b"poster")
    _register_root(database, movies, ZaisanKind.MOVIE)
    fake_client = _scanner(
        database, fake_ffprobe, _probe_result(container="unknown", codec="unknown")
    )
    scanner = _run_scan(database, fake_client)

    result = scanner.audit()
    categories = {finding.category for finding in result.findings}
    assert AuditCategory.AMBIGUOUS_STRUCTURE in categories
    assert AuditCategory.ORPHANED_SUBTITLE in categories
    assert AuditCategory.ORPHANED_POSTER in categories
    assert AuditCategory.SUSPICIOUS_EXTRA in categories
    assert AuditCategory.UNSUPPORTED_CONTAINER in categories
    assert AuditCategory.UNSUPPORTED_CODEC in categories


def test_duplicate_episode_identifier_is_audited(
    database: KatalogDatabase, fake_ffprobe: Path, tmp_path: Path
) -> None:
    shows = tmp_path / "TVShows" / "Example" / "Season 1"
    shows.mkdir(parents=True)
    (shows / "Example S01E01.mkv").write_bytes(b"one")
    (shows / "Example s1e1.webm").write_bytes(b"two")
    _register_root(database, shows.parents[1], ZaisanKind.SERIES)
    fake_client = _scanner(database, fake_ffprobe, _probe_result())
    scanner = _run_scan(database, fake_client)

    result = scanner.scan()
    assert result.totals.added == 1
    assert AuditCategory.DUPLICATE_EPISODE_IDENTIFIER in {
        finding.category for finding in result.findings
    }
    repeat = scanner.scan()
    assert repeat.totals.added == 0
    assert (
        database.run_transaction(lambda session: len(session.scalars(select(MediaFile)).all())) == 1
    )


def test_episode_titles_allow_repeats_and_failed_scan_retries_idempotently(
    database: KatalogDatabase, fake_ffprobe: Path, tmp_path: Path
) -> None:
    season = tmp_path / "TVShows" / "Example" / "Season 4"
    season.mkdir(parents=True)
    for filename in (
        "Example S04E15.mkv",
        "Example S04E16.mkv",
        "S04E17 - The Same Title.mkv",
        "S04E18 - The Same Title.mkv",
        "Z duplicate S04E16.mkv",
    ):
        (season / filename).write_bytes(b"video")
    _register_root(database, season.parents[1], ZaisanKind.SERIES)
    scanner = _run_scan(database, _FakeFfprobeClient(str(fake_ffprobe), _probe_result()))
    scanner.prober = _FailingFfprobeClient(str(fake_ffprobe))

    failed = scanner.scan()
    assert failed.totals.failed == 4
    assert database.run_transaction(lambda session: session.scalar(select(MediaFile))) is None

    scanner.prober = _FakeFfprobeClient(str(fake_ffprobe), _probe_result())
    recovered = scanner.scan()
    assert recovered.totals.added == 4
    assert AuditCategory.DUPLICATE_EPISODE_IDENTIFIER in {
        finding.category for finding in recovered.findings
    }

    def episode_details(session: Session) -> list[tuple[str, str, int]]:
        return [
            (episode.title, episode.sort_title, episode.episode_number or 0)
            for episode in session.scalars(
                select(Zaisan)
                .where(Zaisan.item_kind == ZaisanKind.EPISODE)
                .order_by(Zaisan.episode_number)
            )
        ]

    assert database.run_transaction(episode_details) == [
        ("S04E15", "S04E15", 15),
        ("S04E16", "S04E16", 16),
        ("The Same Title", "The Same Title", 17),
        ("The Same Title", "The Same Title", 18),
    ]

    repeated = scanner.scan()
    assert repeated.totals.added == 0
    assert repeated.totals.unchanged == 4
    assert (
        database.run_transaction(lambda session: len(session.scalars(select(MediaFile)).all())) == 4
    )


def test_ffprobe_normalises_attached_pictures_out_of_playable_video_streams(
    database: KatalogDatabase, fake_ffprobe: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    document = {
        "format": {"format_name": "matroska,webm", "duration": "120.5"},
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "hevc",
                "width": 3840,
                "height": 2160,
                "r_frame_rate": "24000/1001",
                "bits_per_raw_sample": "10",
            },
            {
                "index": 1,
                "codec_type": "video",
                "codec_name": "mjpeg",
                "width": 600,
                "height": 600,
                "tags": {"title": "Cover", "mimetype": "image/jpeg"},
                "disposition": {"attached_pic": 1},
            },
            {
                "index": 2,
                "codec_type": "video",
                "codec_name": "mjpeg",
                "width": 1200,
                "height": 675,
                "tags": {"title": "Backdrop", "mimetype": "image/jpeg"},
                "disposition": {"attached_pic": 1},
            },
            {
                "index": 3,
                "codec_type": "audio",
                "codec_name": "eac3",
                "channel_layout": "5.1(side)",
                "tags": {"language": "eng", "title": "Surround"},
            },
            {
                "index": 4,
                "codec_type": "subtitle",
                "codec_name": "subrip",
                "tags": {"language": "jpn", "title": "Signs"},
                "disposition": {"forced": 1, "default": 0},
            },
            {
                "index": 5,
                "codec_type": "attachment",
                "codec_name": "ttf",
                "tags": {"filename": "Dialogue.otf", "mimetype": "application/x-truetype-font"},
            },
            {
                "index": 6,
                "codec_type": "attachment",
                "codec_name": "bin_data",
                "tags": {"filename": "untrusted/Font.ttf"},
            },
        ],
    }

    async def create_process(*_: object, **__: object) -> _FakeProcess:
        return _FakeProcess(json.dumps(document).encode())

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    result = asyncio.run(FFProbeClient(str(fake_ffprobe)).probe(fake_ffprobe))

    assert result.container == "matroska,webm"
    assert result.duration_seconds == 120.5
    assert result.video_streams == (
        {
            "codec": "hevc",
            "width": 3840,
            "height": 2160,
            "frame_rate": 24000 / 1001,
            "bit_depth": 10,
        },
    )
    assert result.attached_pictures == (
        {
            "index": 1,
            "codec": "mjpeg",
            "width": 600,
            "height": 600,
            "tags": {"title": "Cover", "mimetype": "image/jpeg"},
        },
        {
            "index": 2,
            "codec": "mjpeg",
            "width": 1200,
            "height": 675,
            "tags": {"title": "Backdrop", "mimetype": "image/jpeg"},
        },
    )
    assert result.audio_streams[0]["title"] == "Surround"
    assert result.subtitle_streams[0]["forced"] is True
    assert result.font_attachments == (
        {"stream_index": 5, "filename": "Dialogue.otf", "format": "opentype"},
    )

    movies = tmp_path / "Movies"
    movie = movies / "2000s" / "Artwork.mkv"
    movie.parent.mkdir(parents=True)
    movie.write_bytes(b"video")
    _register_root(database, movies, ZaisanKind.MOVIE)
    scanner = _run_scan(database, _scanner(database, fake_ffprobe, result))

    scan_result = scanner.scan()
    assert AuditCategory.UNSUPPORTED_CODEC not in {
        finding.category for finding in scan_result.findings
    }

    persisted = database.run_transaction(lambda session: session.scalar(select(MediaFile)))
    assert persisted is not None
    assert persisted.video_streams == list(result.video_streams)
    assert persisted.attached_pictures == list(result.attached_pictures)
    assert persisted.font_attachments == list(result.font_attachments)


def test_probe_failure_is_reported_without_cataloguing(
    database: KatalogDatabase, fake_ffprobe: Path, tmp_path: Path
) -> None:
    movies = tmp_path / "Movies"
    movie = movies / "2000s" / "Unreadable.mkv"
    movie.parent.mkdir(parents=True)
    movie.write_bytes(b"not media")
    _register_root(database, movies, ZaisanKind.MOVIE)
    scanner = _run_scan(database, _FakeFfprobeClient(str(fake_ffprobe), _probe_result()))
    scanner.prober = _FailingFfprobeClient(str(fake_ffprobe))

    result = scanner.scan()
    assert result.totals.failed == 1
    assert AuditCategory.UNREADABLE_FILE in {finding.category for finding in result.findings}
    assert database.run_transaction(lambda session: session.scalar(select(MediaFile))) is None


def test_scan_and_audit_cli_commands(
    database: KatalogDatabase, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        katalog_cli,
        "KatalogSettings",
        lambda: KatalogSettings(database_path=Path(database.engine.url.database or "")),
    )

    katalog_cli.main(("scan",))
    assert "Scan summary" in capsys.readouterr().out
    katalog_cli.main(("audit",))
    assert "Needs review" in capsys.readouterr().out

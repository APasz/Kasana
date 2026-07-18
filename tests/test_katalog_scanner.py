from __future__ import annotations

import asyncio
import json
from collections.abc import Generator, Sequence
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from kasana.katalog.cli import app as katalog_cli
from kasana.katalog.database import KatalogDatabase
from kasana.katalog.models import AuditCategory, AvailabilityState, MediaFile, Zaisan, ZaisanKind
from kasana.katalog.parsing import (
    LibraryLayout,
    ParsedMediaKind,
    ParseFailure,
    parse_episode_numbers,
    parse_media_path,
    parse_season_number,
)
from kasana.katalog.probe import FFProbeClient, ProbeFailure, ProbeResult
from kasana.katalog.scanning import IncrementalScanner
from kasana.katalog.services import create_library_root
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
def database(tmp_path: Path) -> Generator[KatalogDatabase]:
    katalog_database = KatalogDatabase(tmp_path / "katalog.sqlite3")
    katalog_database.create_schema()
    yield katalog_database
    katalog_database.close()


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
    assert media_file.container == "matroska,webm"
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


def test_episode_parsing_uses_season_directory_context() -> None:
    assert parse_season_number("Season 02", allow_volume=False) == 2
    assert parse_season_number("Volume 02", allow_volume=False) is None
    assert parse_season_number("Volume 02", allow_volume=True) == 2
    assert parse_episode_numbers("Show s1e2", season_from_directory=1) == (1, 2)
    assert parse_episode_numbers("Show E02", season_from_directory=1) == (1, 2)
    assert parse_episode_numbers("Show E02", season_from_directory=None) is None

    parsed = parse_media_path(
        Path("/library/TVShows"),
        LibraryLayout.TV_SHOWS,
        Path("/library/TVShows/Show/Season 1/Show E02.mkv"),
    )
    assert not isinstance(parsed, ParseFailure)
    assert parsed.kind is ParsedMediaKind.EPISODE
    assert parsed.season_number == 1
    assert parsed.episode_number == 2


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
        ],
    }

    async def create_process(*_: object, **__: object) -> _FakeProcess:
        return _FakeProcess(json.dumps(document).encode())

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    result = asyncio.run(FFProbeClient(str(fake_ffprobe)).probe(fake_ffprobe))

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
    assert "discovered=0" in capsys.readouterr().out
    katalog_cli.main(("audit",))
    assert "ambiguous=0" in capsys.readouterr().out

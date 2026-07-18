# Kasana

Kasana is a personal media catalogue, playback-tracking, and launcher system.
Katalog owns the catalogue, scanner, SQLite database, and HTTP API. Kanvas
(dashboard), Kestrel (player agent), and Kourier (metadata integration) have
their composition roots in place but do not yet run standalone services.

## Start from scratch

Install [uv](https://docs.astral.sh/uv/), Python 3.14, and FFmpeg (`ffprobe`
must be on `PATH`), then run:

```bash
git clone <repository-url> kasana
cd kasana
uv sync --all-groups

export KASANA_KATALOG_DATABASE_PATH="$PWD/.local/share/kasana/kasana.sqlite3"
export KASANA_KATALOG_ARTWORK_CACHE_PATH="$PWD/.cache/kasana/artwork"

uv run kasana-katalog database initialise
uv run kasana-katalog library add /absolute/path/to/Movies --expected-kind movie --display-name Movies
uv run kasana-katalog scan
uv run kasana-katalog status
```

Use `--expected-kind series` for a television root. The scanner recognises
`.avi`, `.m4v`, `.mkv`, `.mov`, `.mp4`, and `.webm`. Settings can instead go in
an ignored `.env` file:

```dotenv
KASANA_KATALOG_DATABASE_PATH=.local/share/kasana/kasana.sqlite3
KASANA_KATALOG_ARTWORK_CACHE_PATH=.cache/kasana/artwork
```

Start the API with `uv run kasana-katalog-api`; its documentation is at
<http://127.0.0.1:5373/api/v1/docs>. Set `KASANA_KATALOG_API_HOST` or
`KASANA_KATALOG_API_PORT` to change the default `127.0.0.1:5373` bind address.

## Common commands

```bash
uv run kasana-katalog library list
uv run kasana-katalog scan --root 1 --probe-concurrency 4
uv run kasana-katalog scan --dry-run
uv run kasana-katalog audit --category orphaned_subtitle
uv run kasana-katalog database upgrade
uv run kasana-katalog --json status

uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run pytest
```

`--json` and `--debug` are global CLI options. Offline roots are retained and
their files marked unavailable; scans do not delete catalogue records.

## Metadata and artwork

TMDB-backed commands need a v4 access token:

```bash
export KASANA_KOURIER_TMDB_API_TOKEN='your-tmdb-v4-access-token'
uv run kasana-katalog metadata auto-match --root 1
uv run kasana-katalog metadata review
uv run kasana-katalog artwork fetch --root 1
```

Matching is reviewable and conservative; fuzzy title similarity alone cannot
auto-match. Downloaded artwork is cached at `KASANA_KATALOG_ARTWORK_CACHE_PATH`
(default `kasana-artwork-cache`) and never replaces artwork in media directories.
TMDB options use the `KASANA_KOURIER_TMDB_` prefix; shared logging uses
`KASANA_LOG_LEVEL`.

See [docs/architecture.md](docs/architecture.md) for component boundaries.

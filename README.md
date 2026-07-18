# Kasana

Kasana is a personal media catalogue, playback-tracking, and launcher system.
Katalog owns the catalogue, scanner, SQLite database, and HTTP API. Kanvas
(dashboard) and Kourier (metadata integration) have their composition roots in
place. Kestrel is the mpv player agent.

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
uv run kasana-katalog user create owner --display-name Owner
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
Kanvas is configured to use `127.0.0.1:5370` when its web server is enabled.

## Play a file with Kestrel

Install `mpv`. Katalog and Kestrel use compatible defaults, so start the API
in a separate terminal and leave it running:

```bash
# Terminal 1
uv run kasana-katalog-api
```

In another terminal, confirm the resolved local configuration, then find the
playback user and item ID. A fresh database can create its first user with
`kasana-katalog user create owner --display-name Owner`.

```bash
# Terminal 2
uv run kasana config show
uv run kasana-katalog user list
uv run kasana-katalog item search Cars --year 2006 --kind movie
```

Play an available item directly; Kestrel creates and consumes the short-lived
Katalog launch token without printing it:

```bash
uv run kasana-kestrel play-item 42 --user owner
```

Kestrel can also start a series queue or an explicit queue:

```bash
uv run kasana-kestrel play-series 8 --user owner --resume
uv run kasana-kestrel play-queue 4 9 12 --user owner
```

On Linux or Steam Deck Desktop Mode, install the per-user URI handler once and
check end-to-end readiness:

```bash
uv run kasana-kestrel install-uri-handler
uv run kasana doctor
```

The URI handler supports `kasana://play/<launch-token>` links from another
Kasana client. `install-uri-handler` prints the exact XDG `.desktop` file it
creates. Use `kasana-kestrel uninstall-uri-handler` to remove it.

## Common commands

```bash
uv run kasana-katalog library list
uv run kasana-katalog scan --root 1 --probe-concurrency 4
uv run kasana-katalog scan --dry-run
uv run kasana-katalog audit --category orphaned_subtitle
uv run kasana-katalog database upgrade
uv run kasana-katalog --json status
uv run kasana-katalog user list
uv run kasana-katalog item search Cars --year 2006 --kind movie

uv run kasana config show
uv run kasana doctor

uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run pytest
```

`--json` and `--debug` are global CLI options. Offline roots are retained and
their files marked unavailable; scans do not delete catalogue records.
Container and codec audit findings mean Katalog does not recognise the reported
FFmpeg format or codec family; they do not claim that the installed mpv/FFmpeg
stack cannot play the file.

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

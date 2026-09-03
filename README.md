# Kasana

Kasana is a self-hosted personal media catalogue for a trusted LAN. It scans local films
and series, tracks each profile's progress, and provides browser-first playback with an
optional local mpv launcher.

| Component | Responsibility |
| --- | --- |
| Katalog | SQLite catalogue, scanning, metadata, artwork, playback state, and the HTTP API. |
| Kanvas | NiceGUI browser dashboard and primary in-browser player. |
| Kestrel | Optional local mpv playback agent and `kasana://` URI handler. |
| Kourier | TMDB and optional Fanart.tv provider adapters. |

## Quick start

Kasana requires [uv](https://docs.astral.sh/uv/) and Python 3.14. Install FFmpeg so
`ffprobe` is on `PATH` for scans; mpv is needed only for Kestrel.

```bash
git clone <repository-url> kasana
cd kasana
uv sync --all-groups

uv run kasana-katalog database initialise
uv run kasana-katalog user create owner --display-name Owner
uv run kasana-katalog library add /absolute/path/to/Movies --expected-kind movie --display-name Movies
uv run kasana-katalog scan
```

Use `--expected-kind series` for a television root
Katalog scans `.avi`, `.m4v`, `.mkv`, `.mov`, `.mp4`, and `.webm` files

Start Katalog, then Kanvas in another terminal:
```bash
uv run kasana-katalog-api
```

```bash
uv run kasana-kanvas
```

Open <http://127.0.0.1:5370>. Kanvas binds to `0.0.0.0:5370` by default; Katalog
binds to `127.0.0.1:5373`. Its OpenAPI documentation is at
<http://127.0.0.1:5373/api/v1/docs>.

This browser-first setup is complete:
neither Kestrel nor mpv is required to scan, browse, or play media in Kanvas.

## Configuration and security

Non-secret preferences live in `configs/config.<domain>.json`,
for `shared`, `katalog`, `kanvas`, `kestrel`, `kourier`, `tmdb`, and `fanart`.
Environment variables and `.env` override those files.
Set `KASANA_CONFIG_DIRECTORY` to relocate the configuration root.

Katalog and Kanvas create `configs/katalog.api-token` and
`configs/kanvas.session-secret`, respectively, with owner-only permissions.
Keep them private.
Components sharing the configuration directory use the API token automatically; a
remote Kestrel client needs the same `KASANA_KATALOG_API_BEARER_TOKEN` value.

Profiles are stored in `configs/users/<id>/configuration.json`.
Their optional PINs are plaintext trusted-LAN convenience gates, not passwords.

## Metadata and artwork

TMDB metadata and artwork require a Read Access Token:

```bash
export KASANA_KOURIER_TMDB_API_TOKEN='your-tmdb-read-access-token'
uv run kasana-katalog metadata auto-match --root 1
uv run kasana-katalog metadata review
uv run kasana-katalog artwork fetch --root 1
```

Fanart.tv is optional and adds artwork variants:

```bash
export KASANA_KOURIER_FANART_API_KEY='your-fanart-project-key'
```

Downloaded artwork is cached outside media directories.
Matching is conservative; review or manually select uncertain matches with `kasana-katalog metadata --help`.

## Playback

Kanvas is the primary player.
Open the dashboard, select a profile, and choose a title to play it in the browser.
Playback uses the browser's native media support, so container and
codec compatibility depends on the browser and host.

Use optional Kestrel only when you prefer local mpv playback or the browser cannot play a file:

```bash
uv run kasana-katalog item search Cars --year 2006 --kind movie
uv run kasana-kestrel play-item 42 --user owner
uv run kasana-kestrel play-series 8 --user owner --resume
```

On an XDG desktop, install and check the URI handler with:

```bash
uv run kasana-kestrel install-uri-handler
uv run kasana-kestrel doctor
```

## Operations and development

```bash
uv run kasana-katalog status
uv run kasana-katalog scan --dry-run
uv run kasana-katalog audit
uv run kasana-katalog database backup

# Stop kasana-katalog-api before restoring.
uv run kasana-katalog database restore kasana.backup.json --yes

uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run pytest
```

Use `--json` for machine-readable Katalog CLI output and `--help` on any command for its
full interface.
See [the architecture notes](docs/architecture.md) for component boundaries
and [the remaining-issues report](docs/remaining-issues.md) for known limits.

## Licence

MIT.

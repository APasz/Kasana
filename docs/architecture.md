# Architecture

Kasana is a small set of independently runnable components with one-way dependencies:

```text
Kanvas ─────┐
Kestrel ────┼──> Katalog v1 HTTP API <── typed aiohttp client
Yukibot ────┤
Remote apps ┘
```

- **Katalog** owns media discovery, persistence, watch state, and the backend API. Its
  FastAPI server publishes `/api/v1`; `kasana.katalog.public` is the only Python surface
  other components may import, and exposes only transport-neutral contracts plus
  `KatalogClient`.
- **Kanvas** renders the NiceGUI dashboard and accesses catalogue data only through
  Katalog's public API/contracts.
- **Kestrel** runs on a playback machine, controls a local mpv or VLC process, and reports
  playback updates through Katalog's public API/contracts.
- **Kourier** fetches and normalises external metadata. It knows nothing about catalogue
  persistence or match decisions.
- **kasana.shared** contains only reusable settings, logging, and stable common contracts,
  including provider-neutral metadata models. It must not depend on any component.

The concrete package layout follows those ownership boundaries: `katalog.metadata` separates
scoring, candidate persistence, review/refresh, and artwork caching; `katalog.scanning`
separates discovery, classification, reconciliation, and its service; and `kourier.tmdb`
separates the HTTP client, validated payloads, mapping, and retry policy. The Katalog CLI's
composition root is `kasana.katalog.cli.app`; `kasana.katalog.__main__` exists only for
`python -m kasana.katalog`.

Metadata integration follows the same boundary: Kourier returns normalised provider data;
Katalog persists bindings, candidates, manual locks, review history, and cached artwork.
Provider adapters never import Katalog persistence. The API runtime and CLI are the
composition roots that construct configured providers for administrative work.

Katalog persistence, filesystem paths, provider adapters, and services are internal
implementation details. Kanvas, Kestrel, Yukibot, Kourier, and remote clients must never
import them directly. The v1 API returns only opaque artwork/playback URLs; it does not
implement media streaming, device pairing, or a dashboard.

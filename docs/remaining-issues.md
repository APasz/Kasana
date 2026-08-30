# Remaining issues

Audit date: 2026-08-30.

- Browser playback is limited by the browser's container and codec support. Kanvas offers
  optional Kestrel/mpv fallback for media the browser cannot render.
- Live TMDB and Fanart.tv matching and artwork fetches require user-provided provider
  credentials and depend on those external services being available.

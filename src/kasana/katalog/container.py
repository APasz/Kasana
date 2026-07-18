"""Pure normalization of FFmpeg format-name alias families."""

from __future__ import annotations

from typing import Literal

type CanonicalContainer = Literal["isobmff", "matroska", "avi"]

_ISOBMFF_ALIASES = frozenset({"mov", "mp4", "m4a", "3gp", "3g2", "mj2"})
_MATROSKA_ALIASES = frozenset({"matroska", "webm"})


def canonical_container(format_name: str) -> CanonicalContainer | None:
    """Map FFmpeg's raw ``format_name`` aliases to one container family.

    FFmpeg reports demuxer aliases as a comma-separated family; the values are
    alternatives, not containers a file must simultaneously satisfy.
    """

    aliases = _format_aliases(format_name)
    if aliases == frozenset({"isobmff"}) or (aliases and aliases <= _ISOBMFF_ALIASES):
        return "isobmff"
    if aliases == frozenset({"matroska"}) or (aliases and aliases <= _MATROSKA_ALIASES):
        return "matroska"
    if aliases == frozenset({"avi"}):
        return "avi"
    return None


def _format_aliases(format_name: str) -> frozenset[str]:
    return frozenset(alias.strip().casefold() for alias in format_name.split(",") if alias.strip())

"""Map Fanart.tv images to Kasana's provider-neutral artwork contracts."""

from __future__ import annotations

from kasana.kourier.fanart.constants import FANART_PROVIDER
from kasana.kourier.fanart.payloads import FanartImagePayload
from kasana.shared.metadata import ArtworkKind, ArtworkReference


def poster_artwork(image: FanartImagePayload) -> ArtworkReference:
    """Map one Fanart.tv movie poster while retaining picker metadata."""

    return ArtworkReference(
        provider=FANART_PROVIDER,
        kind=ArtworkKind.POSTER,
        raw_path=str(image.id),
        source_url=image.url,
        language=None if image.lang == "00" else image.lang,
        width=image.width,
        height=image.height,
        vote_count=image.likes,
    )

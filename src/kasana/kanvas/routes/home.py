"""Kanvas home route."""

from __future__ import annotations

from kasana.kanvas.components.feedback import feedback_state
from kasana.kanvas.components.media_rail import media_rail
from kasana.kanvas.components.shell import page_shell
from kasana.kanvas.services.katalog import KanvasKatalogService
from kasana.kanvas.settings import Kanvas_Settings
from kasana.katalog.public import KatalogClientError, KatalogClientErrorKind


async def render_home(settings: Kanvas_Settings) -> None:
    """Render real continue, on-deck, and recently-added data in compact rails."""

    with page_shell(settings, "/", "Home"):
        try:
            rails = await KanvasKatalogService(settings).home_rails()
        except KatalogClientError as error:
            detail = (
                "Katalog is unavailable."
                if error.kind
                in {KatalogClientErrorKind.TRANSPORT, KatalogClientErrorKind.UNAVAILABLE}
                else "Katalog could not load Home."
            )
            feedback_state("Home is unavailable", detail)
            return
        for rail in rails:
            media_rail(rail)

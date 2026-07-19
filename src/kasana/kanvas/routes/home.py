"""Kanvas home route."""

from __future__ import annotations

from kasana.kanvas.components.browser import BrowserComponent, mount_browser_component
from kasana.kanvas.components.feedback import feedback_state
from kasana.kanvas.components.media_rail import media_rail
from kasana.kanvas.components.shell import page_shell
from kasana.kanvas.services.katalog import KanvasKatalogService
from kasana.kanvas.settings import Kanvas_Settings
from kasana.kanvas.viewmodels.home import MediaRailView
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
        if _needs_artwork_onboarding(rails):
            mount_browser_component(BrowserComponent.ONBOARDING, {"state-key": "first-artwork"})
        for rail in rails:
            media_rail(rail)


def _needs_artwork_onboarding(rails: tuple[MediaRailView, ...]) -> bool:
    """Prompt only for an actual first library whose catalogue posters lack artwork."""

    added = next((rail for rail in rails if rail.title == "Recently Added"), None)
    if added is None or not added.posters:
        return False
    artwork_count = sum(poster.poster_url is not None for poster in added.posters)
    return artwork_count * 4 < len(added.posters)

"""Kanvas About page and required third-party notices."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from nicegui import ui

from kasana.kanvas.components.shell import page_shell
from kasana.kanvas.components.typography import page_title, section_title
from kasana.kanvas.profiles import SessionProfile
from kasana.kanvas.settings import Kanvas_Settings

_GITHUB_URL: Final = "https://github.com/APasz/Kasana"
_TMDB_URL: Final = "https://www.themoviedb.org"
_TMDB_LOGO_PATH: Final = "/_kanvas/tmdb-logo.svg"
_FANART_URL: Final = "https://fanart.tv"
_FANART_API_URL: Final = "https://api.fanart.tv"
_CC_BY_3_URL: Final = "https://creativecommons.org/licenses/by/3.0/"
_MIT_LICENSE_FILENAME: Final = "LICENSE"
_GITHUB_MIT_LICENSE_URL: Final = f"{_GITHUB_URL}/blob/main/{_MIT_LICENSE_FILENAME}"
_SUBTITLES_OCTOPUS_LICENSE_URL: Final = f"/_kanvas/libass/{_MIT_LICENSE_FILENAME}"
_SUBTITLES_OCTOPUS_COPYRIGHT_URL: Final = "/_kanvas/libass/COPYRIGHT"


@dataclass(frozen=True)
class _NoticeLink:
    """One linked resource within a concise notice."""

    label: str
    url: str
    external: bool = True


def render_about(settings: Kanvas_Settings, profile: SessionProfile) -> None:
    """Render project information, legal notices, and provider acknowledgements."""

    with page_shell(settings, "/about", "About", profile):
        with ui.element("article").classes("k-about"):
            page_title("About Kasana")
            ui.label(
                "Kasana is a local-first personal media catalogue, playback tracker, and launcher."
            ).classes("k-about__lead")
            _link("View the source on GitHub", _GITHUB_URL)

            ui.label("Created by APasz").classes("k-about__copy")

            section_title("Licence")
            _notice(
                "Kasana",
                "Released under the MIT License.",
                (_NoticeLink("MIT License", _GITHUB_MIT_LICENSE_URL),),
            )

            section_title("Data and artwork")
            _notice(
                "TMDB",
                "This product uses the TMDB API but is not endorsed or certified by TMDB.",
                (_NoticeLink("The Movie Database", _TMDB_URL),),
                logo_path=_TMDB_LOGO_PATH,
            )
            _notice(
                "Fanart.tv",
                (
                    "The API is available under Creative Commons Attribution 3.0; artwork "
                    "copyrights remain with their respective owners."
                ),
                (
                    _NoticeLink("Fanart.tv", _FANART_URL),
                    _NoticeLink("CC BY 3.0", _CC_BY_3_URL),
                    _NoticeLink("API documentation", _FANART_API_URL),
                ),
            )

            section_title("Bundled notices")
            _notice(
                "JavascriptSubtitlesOctopus",
                "Browser subtitle rendering includes this project and its bundled components.",
                (
                    _NoticeLink("License", _SUBTITLES_OCTOPUS_LICENSE_URL, external=False),
                    _NoticeLink(
                        "Copyright notices", _SUBTITLES_OCTOPUS_COPYRIGHT_URL, external=False
                    ),
                ),
            )


def _notice(
    title: str,
    detail: str,
    links: tuple[_NoticeLink, ...],
    *,
    logo_path: str | None = None,
) -> None:
    with ui.element("section").classes("k-about__notice"):
        ui.label(title).classes("k-about__notice-title")
        if logo_path is not None:
            ui.element("img").classes("k-about__notice-logo").props(
                f'src="{logo_path}" alt="{title}"'
            )
        ui.label(detail).classes("k-about__notice-detail")
        with ui.element("div").classes("k-about__notice-links"):
            for link in links:
                _link(link.label, link.url, external=link.external)


def _link(label: str, url: str, *, external: bool = True) -> None:
    properties = f'href="{url}"'
    if external:
        properties += ' target="_blank" rel="noopener noreferrer"'
    with ui.element("a").classes("k-about__link").props(properties):
        ui.label(label)

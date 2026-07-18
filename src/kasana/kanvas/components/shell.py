"""Kanvas page shell, document metadata, and responsive layout."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from nicegui import ui

from kasana.kanvas.components.navigation import primary_navigation
from kasana.kanvas.settings import Kanvas_Settings


def add_kanvas_head(settings: Kanvas_Settings) -> None:
    """Attach versioned local assets and small page-level policy metadata once."""

    ui.add_head_html(
        """
        <meta name="color-scheme" content="dark">
        <meta name="theme-color" content="#000000">
        <!-- NiceGUI's Vue runtime compiler requires this narrowly scoped exception. -->
        <meta http-equiv="Content-Security-Policy"
              content="default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline';
                       script-src 'self' 'unsafe-inline' 'unsafe-eval'; connect-src 'self';">
        <link rel="stylesheet" href="/_kanvas/kanvas.css">
        <script defer src="/_kanvas/kanvas.js"></script>
        """,
        shared=True,
    )
    ui.add_head_html(f"<style>:root{{--k-accent:{settings.accent_color};}}</style>", shared=True)


@contextmanager
def page_shell(settings: Kanvas_Settings, active_route: str, title: str) -> Generator[None]:
    """Create the same native shell for every first-pass Kanvas page."""

    with ui.element("div").classes("k-app"):
        primary_navigation(active_route)
        with ui.element("main").classes("k-main").props(f'aria-label="{title}"'):
            yield

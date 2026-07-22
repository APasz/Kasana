"""Kanvas page shell, document metadata, and responsive layout."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from nicegui import ui

from kasana.kanvas.components.navigation import primary_navigation
from kasana.kanvas.profiles import SessionProfile
from kasana.kanvas.settings import Kanvas_Settings


@dataclass(frozen=True)
class KanvasAssetVersions:
    """Short content versions for the static assets included by every Kanvas page."""

    css: str
    javascript: str


def kanvas_asset_versions(static_directory: Path) -> KanvasAssetVersions:
    """Return deterministic versions for the assets served from ``static_directory``."""

    return KanvasAssetVersions(
        css=_asset_version(static_directory / "kanvas.css"),
        javascript=_asset_version(static_directory / "kanvas.js"),
    )


def _asset_version(asset_path: Path) -> str:
    return sha256(asset_path.read_bytes()).hexdigest()[:12]


def kanvas_head_html(asset_versions: KanvasAssetVersions) -> str:
    """Build the static document head with content-addressed local asset URLs."""

    return f"""
        <meta name="color-scheme" content="dark">
        <meta name="theme-color" content="#000000">
        <!-- NiceGUI's Vue runtime compiler requires this narrowly scoped exception. -->
        <meta http-equiv="Content-Security-Policy"
              content="default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline';
                       script-src 'self' 'unsafe-inline' 'unsafe-eval'; connect-src 'self';">
        <link rel="stylesheet" href="/_kanvas/kanvas.css?v={asset_versions.css}">
        <link rel="stylesheet" href="/_kanvas/theme.css">
        <script defer src="/_kanvas/kanvas.js?v={asset_versions.javascript}"></script>
        """


def add_kanvas_head(settings: Kanvas_Settings, asset_versions: KanvasAssetVersions) -> None:
    """Attach versioned local assets and small page-level policy metadata once."""

    ui.add_head_html(kanvas_head_html(asset_versions), shared=True)


@contextmanager
def page_shell(
    settings: Kanvas_Settings,
    active_route: str,
    title: str,
    profile: SessionProfile | None = None,
) -> Generator[None]:
    """Create the same native shell for every first-pass Kanvas page."""

    with ui.element("div").classes("k-app"):
        primary_navigation(active_route, profile, settings)
        with ui.element("main").classes("k-main").props(f'aria-label="{title}"'):
            yield

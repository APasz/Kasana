"""Compose the Kanvas application from focused route modules."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from nicegui import app, ui
from starlette.middleware.sessions import SessionMiddleware

from kasana.kanvas.components.shell import add_kanvas_head, kanvas_asset_versions
from kasana.kanvas.katalog_clients import (
    close_katalog_client_pool,
    start_katalog_client_pool,
)
from kasana.kanvas.settings import Kanvas_Settings

from .routes.api_administration import (
    acknowledge_system_alert_data,
    administration_action,
    administration_directories_data,
    administration_duplicates_data,
    administration_hierarchy_data,
    administration_jobs_data,
    administration_metadata_data,
    administration_overview_data,
    administration_roots_data,
    consume_toasts_data,
    system_alerts_data,
)
from .routes.api_collections import (
    apply_watch_order_generation_action,
    artwork,
    collection_member_action,
    collection_picker_data,
    collections_data,
    create_collection_action,
    create_watch_order_action,
    delete_collection_action,
    delete_watch_order_action,
    remove_collection_member_action,
    update_collection_action,
    update_collection_member_action,
    update_watch_order_action,
    watch_order_data,
    watch_order_entry_action,
    watch_order_launch_action,
    watch_order_workspace_data,
)
from .routes.api_library import (
    add_item_to_collection_action,
    item_artwork_fetch_action,
    item_edit_action,
    item_edit_data,
    item_metadata_match_action,
    item_metadata_search_data,
    item_parent_choices_data,
    library_data,
    remove_item_from_collection_action,
)
from .routes.api_playback import (
    PlaybackStreamingResponse,
    complete_current_playback,
    complete_playback,
    create_item_download,
    download_grant,
    playback_compatibility,
    playback_kestrel_fallback,
    playback_media,
    playback_progress,
    playback_subtitle,
    playback_subtitle_font,
    playback_tracks,
)
from .routes.api_profiles import (
    bootstrap_profile,
    create_profile_user,
    current_profile_playback_languages,
    disable_profile_user,
    kanvas_theme_stylesheet,
    select_profile,
    sign_out_profile,
    update_current_profile,
    update_kanvas_preferences,
    update_profile_user,
)
from .routes.design import (
    design_page,
)
from .routes.pages import (
    about_page,
    administration_artwork_page,
    administration_duplicates_page,
    administration_hierarchy_page,
    administration_jobs_page,
    administration_libraries_duplicates_page,
    administration_libraries_hierarchy_page,
    administration_libraries_page,
    administration_metadata_artwork_page,
    administration_metadata_page,
    administration_page,
    collection_detail_page,
    collection_edit_page,
    collection_new_page,
    collections_page,
    home_page,
    item_page,
    library_page,
    play_item_page,
    play_watch_order_page,
    profiles_page,
    watch_order_edit_page,
    watch_order_new_page,
    watch_order_page,
)
from .routes.runtime import configure_runtime, runtime

_STATIC_DIRECTORY = Path(__file__).with_name("static")
_assets_registered = False
_head_registered = False
_pages_registered = False

# NiceGUI 3.14 stringifies a Python bool straight into its bootstrap JavaScript,
# producing `const dark = True;`. This lower-case JavaScript literal keeps the
# page bootstrap valid until the upstream template serialises the value as JSON.
_JAVASCRIPT_DARK_TRUE = cast(bool, "true")


async def _start_katalog_clients() -> None:
    await start_katalog_client_pool(runtime.settings)


async def _close_katalog_clients() -> None:
    await close_katalog_client_pool()


def build_dashboard(settings: Kanvas_Settings | None = None) -> None:
    """Configure static assets, lifecycle hooks, and NiceGUI pages exactly once.

    Reconfiguration is rejected because middleware and static assets retain the first settings.
    """

    global _assets_registered, _head_registered, _pages_registered
    configured_settings = settings or Kanvas_Settings()
    if _assets_registered:
        if runtime.settings != configured_settings:
            raise RuntimeError("Kanvas dashboard is already configured with different settings.")
    else:
        configure_runtime(configured_settings)
    if not _assets_registered:
        lifecycle_app: Any = app
        lifecycle_app.on_startup(_start_katalog_clients)
        lifecycle_app.on_shutdown(_close_katalog_clients)
        app.add_middleware(
            SessionMiddleware,
            secret_key=runtime.settings.session_secret,
            session_cookie=runtime.settings.effective_session_cookie_name,
            max_age=runtime.settings.session_max_age_seconds,
            same_site="lax",
            https_only=runtime.settings.session_cookie_secure,
        )
        app.add_static_files(
            "/_kanvas", _STATIC_DIRECTORY, max_cache_age=runtime.settings.static_max_cache_age
        )
        _assets_registered = True
    if not _head_registered:
        add_kanvas_head(runtime.settings, kanvas_asset_versions(_STATIC_DIRECTORY))
        _head_registered = True
    if _pages_registered:
        return

    _kanvas_page("/profiles", "Kanvas · Profiles")(profiles_page)
    _kanvas_page("/", "Kanvas")(home_page)
    _kanvas_page("/about", "Kanvas · About")(about_page)
    _kanvas_page("/library", "Kanvas · Library")(library_page)
    _kanvas_page("/item/{item_id}", "Kanvas · Item")(item_page)
    _kanvas_page("/play/item/{item_id}", "Kanvas · Playback")(play_item_page)
    _kanvas_page("/play/watch-orders/{watch_order_id}", "Kanvas · Playback")(play_watch_order_page)
    _kanvas_page("/collections", "Kanvas · Collections")(collections_page)
    _kanvas_page("/collections/new", "Kanvas · New collection")(collection_new_page)
    _kanvas_page("/collections/{collection_id}/edit", "Kanvas · Edit collection")(
        collection_edit_page
    )
    _kanvas_page("/collections/{collection_id}/watch-orders/new", "Kanvas · New watch order")(
        watch_order_new_page
    )
    _kanvas_page("/collections/{collection_id}", "Kanvas · Collection")(collection_detail_page)
    _kanvas_page("/watch-orders/{watch_order_id}", "Kanvas · Watch order")(watch_order_page)
    _kanvas_page("/watch-orders/{watch_order_id}/edit", "Kanvas · Edit watch order")(
        watch_order_edit_page
    )
    _kanvas_page("/administration", "Kanvas · Administration")(administration_page)
    _kanvas_page("/administration/metadata", "Kanvas · Metadata review")(
        administration_metadata_page
    )
    _kanvas_page("/administration/libraries", "Kanvas · Library roots")(
        administration_libraries_page
    )
    _kanvas_page("/administration/libraries/hierarchy", "Kanvas · Library structure")(
        administration_libraries_hierarchy_page
    )
    _kanvas_page("/administration/libraries/duplicates", "Kanvas · Duplicate resolution")(
        administration_libraries_duplicates_page
    )
    _kanvas_page("/administration/jobs", "Kanvas · Jobs")(administration_jobs_page)
    _kanvas_page("/administration/metadata/artwork", "Kanvas · Artwork maintenance")(
        administration_metadata_artwork_page
    )
    _kanvas_page("/administration/artwork", "Kanvas · Artwork maintenance")(
        administration_artwork_page
    )
    _kanvas_page("/administration/hierarchy", "Kanvas · Hierarchy repair")(
        administration_hierarchy_page
    )
    _kanvas_page("/administration/duplicates", "Kanvas · Duplicate resolution")(
        administration_duplicates_page
    )
    _kanvas_page("/_design", "Kanvas · Design review")(design_page)
    _pages_registered = True


def _kanvas_page(path: str, title: str) -> ui.page:
    """Create a Kanvas page with a browser-valid NiceGUI dark-mode literal."""

    return ui.page(path, title=title, dark=_JAVASCRIPT_DARK_TRUE)


__all__ = (
    "PlaybackStreamingResponse",
    "about_page",
    "acknowledge_system_alert_data",
    "add_item_to_collection_action",
    "administration_action",
    "administration_artwork_page",
    "administration_directories_data",
    "administration_duplicates_data",
    "administration_duplicates_page",
    "administration_hierarchy_data",
    "administration_hierarchy_page",
    "administration_jobs_data",
    "administration_jobs_page",
    "administration_libraries_duplicates_page",
    "administration_libraries_hierarchy_page",
    "administration_libraries_page",
    "administration_metadata_artwork_page",
    "administration_metadata_data",
    "administration_metadata_page",
    "administration_overview_data",
    "administration_page",
    "administration_roots_data",
    "apply_watch_order_generation_action",
    "artwork",
    "bootstrap_profile",
    "build_dashboard",
    "collection_detail_page",
    "collection_edit_page",
    "collection_member_action",
    "collection_new_page",
    "collection_picker_data",
    "collections_data",
    "collections_page",
    "complete_current_playback",
    "complete_playback",
    "consume_toasts_data",
    "create_collection_action",
    "create_item_download",
    "create_profile_user",
    "create_watch_order_action",
    "current_profile_playback_languages",
    "delete_collection_action",
    "delete_watch_order_action",
    "design_page",
    "disable_profile_user",
    "download_grant",
    "home_page",
    "item_artwork_fetch_action",
    "item_edit_action",
    "item_edit_data",
    "item_metadata_match_action",
    "item_metadata_search_data",
    "item_page",
    "item_parent_choices_data",
    "kanvas_theme_stylesheet",
    "library_data",
    "library_page",
    "play_item_page",
    "play_watch_order_page",
    "playback_compatibility",
    "playback_kestrel_fallback",
    "playback_media",
    "playback_progress",
    "playback_subtitle",
    "playback_subtitle_font",
    "playback_tracks",
    "profiles_page",
    "remove_collection_member_action",
    "remove_item_from_collection_action",
    "select_profile",
    "sign_out_profile",
    "system_alerts_data",
    "update_collection_action",
    "update_collection_member_action",
    "update_current_profile",
    "update_kanvas_preferences",
    "update_profile_user",
    "update_watch_order_action",
    "watch_order_data",
    "watch_order_edit_page",
    "watch_order_entry_action",
    "watch_order_launch_action",
    "watch_order_new_page",
    "watch_order_page",
    "watch_order_workspace_data",
)

"""Development-only Kanvas design-review page."""

from __future__ import annotations

from fastapi import HTTPException
from nicegui import ui

from kasana.kanvas.components.collections import (
    collection_tile,
    generation_preview,
    item_picker_overlay,
    watch_order_card,
)
from kasana.kanvas.components.controls import IconName, action_button, icon_action
from kasana.kanvas.components.feedback import feedback_state, skeleton_posters
from kasana.kanvas.components.inputs import text_input
from kasana.kanvas.components.poster import poster_card
from kasana.kanvas.components.progress import progress_indicator
from kasana.kanvas.components.shell import page_shell
from kasana.kanvas.components.typography import page_title, section_title
from kasana.kanvas.viewmodels.collections import (
    CollectionTileView,
    GenerationPreviewView,
    WatchOrderCardView,
    WatchOrderRowView,
)
from kasana.kanvas.viewmodels.library import (
    PosterState,
    PosterView,
)

from .runtime import runtime


async def design_page() -> None:
    """Render an unlinked development-only component and token review surface."""

    if not runtime.settings.design_route_enabled:
        raise HTTPException(status_code=404, detail="Design review is disabled.")
    with page_shell(runtime.settings, "", "Kanvas design review"):
        page_title("Kanvas design review")
        section_title("Tokens")
        with ui.element("div").classes("k-token-grid"):
            for token in (
                "--k-bg",
                "--k-surface-1",
                "--k-surface-2",
                "--k-surface-active",
                "--k-border-subtle",
                "--k-border-strong",
                "--k-text",
                "--k-text-muted",
                "--k-text-faint",
                "--k-accent",
                "--k-accent-contrast",
                "--k-danger",
                "--k-success",
                "--k-scrim-soft",
                "--k-scrim",
                "--k-scrim-strong",
                "--k-nav-backdrop",
                "--k-poster-placeholder-bg",
                "--k-poster-placeholder-highlight",
                "--k-poster-placeholder-border",
                "--k-poster-placeholder-footer-bg",
            ):
                with ui.element("div").classes("k-token"):
                    ui.element("span").classes("k-token__swatch").style(f"background: var({token})")
                    ui.label(token).classes("k-token__name")
        section_title("Controls and focus")
        with ui.element("div").classes("k-action-row"):
            action_button("Primary", primary=True)
            action_button("Secondary")
            icon_action("Play", IconName.PLAY)
        text_input(name="review", placeholder="Input", aria_label="Review input")
        section_title("Poster states")
        with ui.element("div").classes("k-design-poster-grid"):
            for index, state in enumerate(PosterState):
                poster_card(
                    PosterView(
                        id=index + 1,
                        title=state.value.replace("_", " ").title(),
                        detail="2001 · Movie",
                        href=f"/item/{index + 1}",
                        progressPercent=42 if state is PosterState.IN_PROGRESS else None,
                        state=state,
                        watched=state is PosterState.WATCHED,
                        available=state is not PosterState.UNAVAILABLE,
                    )
                )
        section_title("Progress and feedback")
        progress_indicator(62)
        skeleton_posters(4)
        feedback_state("Empty state", "A quiet, local state for no matching items.")
        feedback_state("Request failed", "A compact retry state.", retry=lambda: None)
        section_title("Collections and watch orders")
        with ui.element("div").classes("k-collection-grid"):
            collection_tile(
                CollectionTileView(
                    id=1,
                    name="Mixed collection",
                    itemCount=4,
                    watchOrderCount=1,
                    revision=1,
                    mosaicUrls=("/kanvas/artwork/1/1", "/kanvas/artwork/2/2"),
                )
            )
        with ui.element("div").classes("k-watch-order-grid"):
            watch_order_card(
                WatchOrderCardView(
                    id=1,
                    collectionId=1,
                    name="Release order",
                    kind="custom",
                    entryCount=4,
                    revision=1,
                    progressPercent=42,
                    nextItemTitle="Pilot",
                    hasUnavailableEntries=True,
                )
            )
        item_picker_overlay(
            source="/kanvas/data/collections/1/picker",
            action="/kanvas/actions/collections/1/members",
            revision=1,
            playable_only=False,
            label="Picker state",
        )
        generation_preview(
            GenerationPreviewView(
                watchOrderId=1,
                revision=1,
                mode="air",
                applyMode="replace",
                entries=(
                    WatchOrderRowView(
                        id=1,
                        position=0,
                        itemId=1,
                        title="Pilot",
                        kind="episode",
                        available=True,
                    ),
                ),
                undatedTitles=("Undated special",),
                unavailableTitles=("Missing episode",),
                duplicateTitles=("Pilot",),
                nonPlayableTitles=("Series container",),
                removedEntryTitles=("Old order",),
            ),
            apply_action="/kanvas/actions/watch-orders/1/apply-generation",
        )
        with ui.element("div").classes("k-conflict-state"):
            ui.label("Revision conflict state.")
            action_button("Reload")
            action_button("Reapply")
        section_title("Administration states")
        ui.html(
            """
            <div class="k-admin-list">
                <article class="k-job-row">
                    <div class="k-job-row__progress">
                        <span class="k-progress-edge k-progress-edge--unknown"></span>
                    </div>
                    <div class="k-job-row__summary">
                        <strong>Queued scan</strong><small>queued · waiting</small>
                    </div>
                    <div class="k-job-row__actions">
                        <button type="button" class="k-button">Details</button>
                        <button type="button" class="k-button">Cancel</button>
                    </div>
                </article>
                <article class="k-job-row">
                    <div class="k-job-row__progress">
                        <span class="k-progress-edge"><span style="--k-progress:62%"></span></span>
                    </div>
                    <div class="k-job-row__summary">
                        <strong>Running artwork</strong><small>running · 62/100 artwork</small>
                    </div>
                    <div class="k-job-row__actions">
                        <button type="button" class="k-button">Details</button>
                        <button type="button" class="k-button">Cancel</button>
                    </div>
                </article>
                <article class="k-job-row">
                    <div class="k-job-row__progress">
                        <span class="k-progress-edge"></span>
                    </div>
                    <div class="k-job-row__summary">
                        <strong>Failed scan</strong><small>failed · matching · 358/358 files</small>
                    </div>
                    <div class="k-job-row__actions">
                        <button type="button" class="k-button">Details</button>
                        <button type="button" class="k-button k-button--danger">Clear</button>
                    </div>
                    <section class="k-job-row__details">
                        <div>TMDB returned HTTP 404.</div>
                        <small>Submitted just now</small>
                    </section>
                </article>
                <article class="k-root-row">
                    <div><strong>Unavailable root</strong><small>movie · offline</small></div>
                    <div><small>Edit or scan when available</small></div>
                </article>
                <div class="k-admin-status">
                    Provider unavailable · candidate selected / rejected / matched
                    · destructive confirmation
                </div>
            </div>
            """
        )

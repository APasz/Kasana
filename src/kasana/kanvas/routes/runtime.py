"""Mutable application configuration shared by Kanvas route modules."""

from __future__ import annotations

from dataclasses import dataclass

from kasana.kanvas.settings import Kanvas_Settings


@dataclass
class KanvasRouteRuntime:
    """Own the configuration used by routes after dashboard composition."""

    settings: Kanvas_Settings


runtime = KanvasRouteRuntime(settings=Kanvas_Settings())


def configure_runtime(settings: Kanvas_Settings) -> None:
    """Update route configuration without invalidating imported runtime references."""

    runtime.settings = settings

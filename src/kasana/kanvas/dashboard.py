"""NiceGUI dashboard composition."""

from nicegui import ui


def build_dashboard() -> None:
    """Register the initial Kanvas dashboard content."""

    ui.label("Kasana Kanvas")

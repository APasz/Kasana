"""Kanvas command-line entry point."""

import logging

from nicegui import ui

from kasana.kanvas.dashboard import build_dashboard
from kasana.kanvas.settings import Kanvas_Settings
from kasana.shared import LogDomain, SharedSettings, configure_logging

LOGGER = logging.getLogger(__name__)


def main() -> None:
    """Configure logging without starting a server (useful to embedding callers)."""

    shared_settings = SharedSettings()
    settings = Kanvas_Settings()
    configure_logging(shared_settings.log_level, LogDomain.KANVAS, shared_settings.log_directory)
    LOGGER.info("Kanvas scaffold configured for %s:%s", settings.host, settings.port)


def console_main() -> None:
    """Run the local Kanvas NiceGUI process."""

    main()
    settings = Kanvas_Settings()
    build_dashboard(settings)
    ui.run(  # pyright: ignore[reportUnknownMemberType]
        host=settings.host,
        port=settings.port,
        title="Kanvas",
        dark=True,
        reload=False,
        tailwind=False,
        show=settings.auto_browser_open,
        show_welcome_message=False,
        log_config=None,
    )
if __name__ == "__main__":  # pragma: no cover
    console_main()

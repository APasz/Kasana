"""Kestrel command-line entry point."""

import logging

from kasana.kestrel.settings import KestrelSettings
from kasana.shared import SharedSettings, configure_logging


def main() -> None:
    shared_settings: SharedSettings = SharedSettings()
    settings: KestrelSettings = KestrelSettings()
    configure_logging(shared_settings.log_level)
    logging.getLogger(__name__).info(
        "Kestrel scaffold configured for %s with %s", settings.katalog_url, settings.player_backend
    )


if __name__ == "__main__":  # pragma: no cover
    main()

"""Kourier command-line entry point."""

import logging

from kasana.kourier.settings import KourierSettings
from kasana.shared import LogDomain, SharedSettings, configure_logging


def main() -> None:
    shared_settings: SharedSettings = SharedSettings()
    settings: KourierSettings = KourierSettings()
    configure_logging(shared_settings.log_level, LogDomain.KOURIER, shared_settings.log_directory)
    logging.getLogger(__name__).info("Kourier configured for %s", settings.katalog_url)


if __name__ == "__main__":  # pragma: no cover
    main()

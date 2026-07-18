"""Kanvas command-line entry point."""

import logging

from kasana.kanvas.settings import Kanvas_Settings
from kasana.shared import SharedSettings, configure_logging


def main() -> None:
    shared_settings = SharedSettings()
    settings = Kanvas_Settings()
    configure_logging(shared_settings.log_level)
    logging.getLogger(__name__).info(
        "Kanvas scaffold configured for %s:%s", settings.host, settings.port
    )


if __name__ == "__main__":  # pragma: no cover
    main()

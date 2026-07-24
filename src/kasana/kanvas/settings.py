"""Kanvas process configuration."""

from pydantic import Field, HttpUrl
from pydantic_settings import SettingsConfigDict

from kasana.configuration import configured_katalog_api_url, kanvas_session_secret
from kasana.shared.profile_rules import (
    PROFILE_ACCENT_COLOUR_DEFAULT,
    PROFILE_ACCENT_COLOUR_PATTERN,
)
from kasana.shared.settings import KSettings


class Kanvas_Settings(KSettings):
    """Settings for the local Kanvas presentation process."""

    configuration_section = "kanvas"
    model_config = SettingsConfigDict(
        env_prefix="KASANA_KANVAS_",
    )

    host: str = "0.0.0.0"
    port: int = Field(default=5370, ge=1, le=65535)
    katalog_url: HttpUrl = Field(default_factory=lambda: HttpUrl(configured_katalog_api_url()))
    session_secret: str = Field(default_factory=kanvas_session_secret, min_length=32, repr=False)
    session_cookie_secure: bool = False
    design_route_enabled: bool = False
    auto_browser_open: bool = False
    development_mode: bool = False
    accent_colour: str = Field(
        default=PROFILE_ACCENT_COLOUR_DEFAULT, pattern=PROFILE_ACCENT_COLOUR_PATTERN
    )
    katalog_timeout_seconds: float = Field(default=8.0, gt=0, le=60)
    ffmpeg_executable: str = "ffmpeg"

    @property
    def static_max_cache_age(self) -> int:
        """Disable static caching only for an explicitly local development process."""

        return 0 if self.development_mode else 3600


KanvasSettings = Kanvas_Settings

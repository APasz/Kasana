"""Kanvas process configuration."""

from hashlib import sha256

from pydantic import Field, HttpUrl, field_validator
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
    session_cookie_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
        pattern=r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$",
    )
    session_max_age_seconds: int = Field(default=14 * 24 * 60 * 60, ge=60, le=90 * 24 * 60 * 60)
    session_cookie_secure: bool = False
    design_route_enabled: bool = False
    auto_browser_open: bool = False
    development_mode: bool = False
    accent_colour: str = Field(
        default=PROFILE_ACCENT_COLOUR_DEFAULT, pattern=PROFILE_ACCENT_COLOUR_PATTERN
    )
    katalog_timeout_seconds: float = Field(default=8.0, gt=0, le=60)
    profile_cache_ttl_seconds: int = Field(default=30, ge=1, le=10 * 60)
    download_public_url: HttpUrl | None = None
    ffmpeg_executable: str = "ffmpeg"

    @field_validator("download_public_url")
    @classmethod
    def validate_download_public_url(cls, value: HttpUrl | None) -> HttpUrl | None:
        """Require an origin because Kanvas appends the fixed Katalog API path."""

        if value is not None and (
            value.path not in {"", "/"}
            or value.query is not None
            or value.fragment is not None
            or value.username is not None
            or value.password is not None
        ):
            raise ValueError(
                "download_public_url must be an origin without a path, query, fragment, "
                "or credentials."
            )
        return value

    @property
    def static_max_cache_age(self) -> int:
        """Disable static caching only for an explicitly local development process."""

        return 0 if self.development_mode else 3600

    @property
    def effective_session_cookie_name(self) -> str:
        """Separate browser cookies for distinct Kanvas and Katalog service instances."""

        if self.session_cookie_name is not None:
            return self.session_cookie_name
        instance_key = f"{self.host}:{self.port}|{self.katalog_url}"
        instance_hash = sha256(instance_key.encode("utf-8")).hexdigest()[:12]
        return f"kanvas_session_{instance_hash}"


KanvasSettings = Kanvas_Settings

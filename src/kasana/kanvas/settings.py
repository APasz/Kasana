"""Kanvas process configuration."""

from pydantic import Field, HttpUrl
from pydantic_settings import SettingsConfigDict

from kasana.shared.settings import KSettings


class Kanvas_Settings(KSettings):
    """Settings for the local Kanvas presentation process."""

    model_config = SettingsConfigDict(
        env_prefix="KASANA_KANVAS_",
    )

    host: str = "127.0.0.1"
    port: int = Field(default=5370, ge=1, le=65535)
    katalog_url: HttpUrl = HttpUrl("http://127.0.0.1:5373")
    user_id: int = Field(default=1, gt=0)
    design_route_enabled: bool = True
    auto_browser_open: bool = False
    development_mode: bool = False
    accent_color: str = Field(default="#e8e8e8", pattern=r"^#[0-9A-Fa-f]{6}$")
    katalog_timeout_seconds: float = Field(default=8.0, gt=0, le=60)

    @property
    def static_max_cache_age(self) -> int:
        """Disable static caching only for an explicitly local development process."""

        return 0 if self.development_mode else 3600


KanvasSettings = Kanvas_Settings

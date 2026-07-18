"""Kanvas process configuration."""

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from kasana.shared.settings import KSettings


class Kanvas_Settings(KSettings):
    model_config = SettingsConfigDict(
        env_prefix="KASANA_KANVAS_",
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1, le=65535)

"""Kourier process and provider configuration."""

from pydantic import AnyHttpUrl, AnyUrl, Field, SecretStr
from pydantic_settings import SettingsConfigDict

from kasana.shared.settings import KSettings


class KourierSettings(KSettings):
    model_config = SettingsConfigDict(
        env_prefix="KASANA_KOURIER_",
    )

    katalog_url: AnyUrl = AnyUrl("http://127.0.0.1:8765")


class TMDBSettings(KSettings):
    """TMDB adapter settings loaded from ``KASANA_KOURIER_TMDB_`` variables."""

    model_config = SettingsConfigDict(
        env_prefix="KASANA_KOURIER_TMDB_",
    )

    api_token: SecretStr
    base_url: AnyHttpUrl = AnyHttpUrl("https://api.themoviedb.org/3")
    image_base_url: AnyHttpUrl = AnyHttpUrl("https://image.tmdb.org/t/p/original")
    language: str = Field(default="en-AU", min_length=2, max_length=32)
    region: str = Field(default="AU", min_length=2, max_length=3)
    timeout_seconds: float = Field(default=10.0, gt=0.0, le=120.0)
    concurrency: int = Field(default=4, ge=1, le=32)
    max_retries: int = Field(default=2, ge=0, le=5)
    retry_backoff_seconds: float = Field(default=0.25, ge=0.0, le=30.0)
    max_backoff_seconds: float = Field(default=5.0, gt=0.0, le=120.0)

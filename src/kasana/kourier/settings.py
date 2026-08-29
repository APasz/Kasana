"""Kourier process and provider configuration."""

from pydantic import AnyHttpUrl, AnyUrl, Field, SecretStr, field_validator
from pydantic_settings import SettingsConfigDict

from kasana.configuration import configured_katalog_api_url
from kasana.shared.settings import KSettings


class KourierSettings(KSettings):
    configuration_section = "kourier"
    model_config = SettingsConfigDict(
        env_prefix="KASANA_KOURIER_",
    )

    katalog_url: AnyUrl = Field(default_factory=lambda: AnyUrl(configured_katalog_api_url()))


class TMDBSettings(KSettings):
    """TMDB adapter settings loaded from ``KASANA_KOURIER_TMDB_`` variables."""

    configuration_section = "tmdb"
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


class FanartSettings(KSettings):
    """Fanart.tv adapter settings loaded from ``KASANA_KOURIER_FANART_`` variables."""

    configuration_section = "fanart"
    model_config = SettingsConfigDict(
        env_prefix="KASANA_KOURIER_FANART_",
    )

    api_key: SecretStr | None = None
    client_key: SecretStr | None = None
    base_url: AnyHttpUrl = AnyHttpUrl("https://webservice.fanart.tv/v3.2")
    language: str = Field(default="en-AU", min_length=2, max_length=32)
    timeout_seconds: float = Field(default=10.0, gt=0.0, le=120.0)
    concurrency: int = Field(default=4, ge=1, le=32)
    max_retries: int = Field(default=2, ge=0, le=5)
    retry_backoff_seconds: float = Field(default=0.25, ge=0.0, le=30.0)
    max_backoff_seconds: float = Field(default=5.0, gt=0.0, le=120.0)

    @field_validator("api_key", "client_key")
    @classmethod
    def credentials_must_not_be_blank(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().strip():
            raise ValueError("Fanart.tv credentials must not be blank.")
        return value

    @property
    def is_configured(self) -> bool:
        """Return whether a Fanart.tv credential can activate this optional provider."""

        return self.api_key is not None or self.client_key is not None

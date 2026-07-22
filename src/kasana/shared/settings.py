"""Settings that apply to every Kasana process."""

from pathlib import Path
from typing import ClassVar

from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from kasana.configuration import ApplicationConfigurationSettingsSource
from kasana.shared.logging import LogLevel


class KSettings(BaseSettings):
    """Environment-backed settings shared by component entry points."""

    model_config = SettingsConfigDict(
        str_strip_whitespace=True,
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        arbitrary_types_allowed=True,
        extra="ignore",
    )

    configuration_section: ClassVar[str | None] = None
    log_level: LogLevel = LogLevel.INFO
    log_file: Path | None = Path("logs/kasana.log")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            ApplicationConfigurationSettingsSource(settings_cls),
            dotenv_settings,
            file_secret_settings,
        )


class SharedSettings(KSettings):
    """Environment-backed settings shared by component entry points."""

    model_config = SettingsConfigDict(
        str_strip_whitespace=True,
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        arbitrary_types_allowed=True,
        env_prefix="KASANA_",
        extra="ignore",
    )

    configuration_section = "shared"
    log_level: LogLevel = LogLevel.INFO

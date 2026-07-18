"""Settings that apply to every Kasana process."""

from pydantic_settings import BaseSettings, SettingsConfigDict

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

    log_level: LogLevel = LogLevel.INFO


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

    log_level: LogLevel = LogLevel.INFO

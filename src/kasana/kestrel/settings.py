"""Kestrel process configuration."""

from enum import StrEnum

from pydantic_settings import SettingsConfigDict

from kasana.shared.settings import KSettings


class PlayerBackend(StrEnum):
    MPV = "mpv"
    VLC = "vlc"


class KestrelSettings(KSettings):
    model_config = SettingsConfigDict(
        env_prefix="KASANA_KESTREL_",
    )

    player_backend: PlayerBackend = PlayerBackend.MPV
    katalog_url: str = "http://127.0.0.1:8765"

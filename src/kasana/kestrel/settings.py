"""Kestrel process configuration."""

from __future__ import annotations

import os
import tempfile
from enum import StrEnum
from pathlib import Path

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from kasana.shared.settings import KSettings


class PlayerBackend(StrEnum):
    MPV = "mpv"
    VLC = "vlc"


def _default_runtime_directory() -> Path:
    runtime_root = Path(os.environ.get("XDG_RUNTIME_DIR", tempfile.gettempdir()))
    return runtime_root / "kasana-kestrel"


class KestrelSettings(KSettings):
    model_config = SettingsConfigDict(
        env_prefix="KASANA_KESTREL_",
    )

    player_backend: PlayerBackend = PlayerBackend.MPV
    katalog_url: str = "http://127.0.0.1:5373"
    mpv_executable: str = "mpv"
    runtime_directory: Path = Field(default_factory=_default_runtime_directory)
    temporary_directory: Path = Field(default_factory=lambda: Path(tempfile.gettempdir()))
    ipc_connect_timeout_seconds: float = Field(default=8.0, gt=0, le=60)
    progress_interval_seconds: float = Field(default=10.0, gt=0, le=300)
    progress_position_delta_seconds: float = Field(default=1.0, gt=0, le=60)
    completion_threshold: float = Field(default=0.9, gt=0, le=1)

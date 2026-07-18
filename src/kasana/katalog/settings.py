"""Katalog process configuration."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from kasana.shared.settings import KSettings


class KatalogSettings(KSettings):
    model_config = SettingsConfigDict(
        env_prefix="KASANA_KATALOG_",
    )

    library_root: Path = Field(default=Path("media"))
    database_path: Path = Field(default=Path("kasana.sqlite3"))
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=5373, ge=1, le=65535)
    video_extensions: frozenset[str] = frozenset({".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm"})
    probe_concurrency: int = Field(default=4, ge=1, le=16)
    ffprobe_executable: str = "ffprobe"
    metadata_auto_match_threshold: float = Field(default=0.94, ge=0.0, le=1.0)
    metadata_suggestion_threshold: float = Field(default=0.70, ge=0.0, le=1.0)
    metadata_ambiguity_margin: float = Field(default=0.08, ge=0.0, le=1.0)
    metadata_batch_size: int = Field(default=50, ge=1, le=500)
    artwork_cache_path: Path = Field(default=Path("kasana-artwork-cache"))
    artwork_concurrency: int = Field(default=4, ge=1, le=16)
    artwork_max_size_bytes: int = Field(default=10 * 1024 * 1024, ge=1, le=100 * 1024 * 1024)

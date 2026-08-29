"""Validated shapes returned by Fanart.tv artwork endpoints."""

from __future__ import annotations

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator


class FanartImagePayload(BaseModel):
    """One Fanart.tv image variant from a version 3.2 response."""

    model_config = ConfigDict(extra="ignore")

    id: str | int
    url: AnyHttpUrl
    lang: str | None = Field(default=None, max_length=32)
    likes: int = Field(default=0, ge=0)
    width: int | None = Field(default=None, ge=1, le=20_000)
    height: int | None = Field(default=None, ge=1, le=20_000)

    @field_validator("id")
    @classmethod
    def image_id_is_not_blank(cls, value: str | int) -> str | int:
        """Reject response records that cannot form a stable artwork revision."""

        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("Fanart.tv image IDs must not be blank.")
        if len(str(value)) > 500:
            raise ValueError("Fanart.tv image IDs must be at most 500 characters.")
        return value


class FanartMoviePayload(BaseModel):
    """The movie-specific artwork fields needed by Kasana's poster picker."""

    model_config = ConfigDict(extra="ignore")

    movieposter: tuple[FanartImagePayload, ...] = ()

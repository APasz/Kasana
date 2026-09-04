"""Typed, ephemeral messages for completed or failed user actions."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ToastSeverity(StrEnum):
    """The presentation and urgency of one short-lived action message."""

    SUCCESS = "success"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ToastView(BaseModel):
    """One bounded message that can safely cross the server/browser boundary."""

    model_config = ConfigDict(frozen=True)

    severity: ToastSeverity
    title: str = Field(min_length=1, max_length=120)
    detail: str | None = Field(default=None, min_length=1, max_length=400)

    @field_validator("title", "detail")
    @classmethod
    def reject_blank_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Toast text cannot be blank.")
        return value


class ToastFeedView(BaseModel):
    """The small, one-time batch consumed by a freshly rendered Kanvas page."""

    model_config = ConfigDict(frozen=True)

    toasts: tuple[ToastView, ...] = Field(default=(), max_length=4)

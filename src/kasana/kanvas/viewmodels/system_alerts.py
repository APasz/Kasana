"""Typed, shell-level operational alerts for the Kanvas UI."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SystemAlertCode(StrEnum):
    """Stable causes that Kanvas can safely explain to a viewer."""

    BROWSER_OFFLINE = "browser_offline"
    KANVAS_UNAVAILABLE = "kanvas_unavailable"
    KATALOG_UNAVAILABLE = "katalog_unavailable"
    DATABASE_UNHEALTHY = "database_unhealthy"
    LIBRARY_ROOT_UNAVAILABLE = "library_root_unavailable"
    MAINTENANCE_JOBS_FAILED = "maintenance_jobs_failed"


class SystemAlertSeverity(StrEnum):
    """The urgency of one active system condition."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


_DURABLE_SYSTEM_ALERT_CODES = frozenset(
    {
        SystemAlertCode.DATABASE_UNHEALTHY,
        SystemAlertCode.LIBRARY_ROOT_UNAVAILABLE,
        SystemAlertCode.MAINTENANCE_JOBS_FAILED,
    }
)


class SystemAlertActionKind(StrEnum):
    """The explicit user action supported by a shell alert."""

    NAVIGATE = "navigate"
    RETRY = "retry"


class SystemAlertActionView(BaseModel):
    """A safe, typed action shown beside one alert."""

    model_config = ConfigDict(frozen=True)

    kind: SystemAlertActionKind
    label: str = Field(min_length=1, max_length=80)
    href: str | None = Field(default=None, max_length=500, pattern=r"^/[^\s]*$")

    @model_validator(mode="after")
    def validate_target(self) -> SystemAlertActionView:
        if self.kind is SystemAlertActionKind.NAVIGATE and self.href is None:
            raise ValueError("Navigation alerts require an internal destination.")
        if self.kind is SystemAlertActionKind.RETRY and self.href is not None:
            raise ValueError("Retry alerts cannot specify a destination.")
        if self.href is not None and (
            self.href.startswith("//")
            or "\\" in self.href
            or any(character.isspace() for character in self.href)
        ):
            raise ValueError("Navigation alerts must use an internal destination.")
        return self


class SystemAlertView(BaseModel):
    """One current, non-dismissible condition in the Kanvas shell."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,99}$")
    code: SystemAlertCode
    severity: SystemAlertSeverity
    title: str = Field(min_length=1, max_length=160)
    detail: str = Field(min_length=1, max_length=500)
    action: SystemAlertActionView
    incident_id: int | None = Field(default=None, gt=0, alias="incidentId")
    acknowledged_at: datetime | None = Field(default=None, alias="acknowledgedAt")

    @model_validator(mode="after")
    def validate_incident_metadata(self) -> SystemAlertView:
        if self.acknowledged_at is not None and self.incident_id is None:
            raise ValueError("Acknowledged system alerts require an incident identifier.")
        if self.incident_id is not None and self.code not in _DURABLE_SYSTEM_ALERT_CODES:
            raise ValueError("Only durable system alerts can have an incident identifier.")
        return self


class SystemAlertHistoryView(BaseModel):
    """One recovered, administrator-visible operational incident."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    incident_id: int = Field(gt=0, alias="incidentId")
    code: SystemAlertCode
    severity: SystemAlertSeverity
    title: str = Field(min_length=1, max_length=160)
    detail: str = Field(min_length=1, max_length=500)
    first_detected_at: datetime = Field(alias="firstDetectedAt")
    last_detected_at: datetime = Field(alias="lastDetectedAt")
    resolved_at: datetime = Field(alias="resolvedAt")
    acknowledged_at: datetime | None = Field(default=None, alias="acknowledgedAt")

    @model_validator(mode="after")
    def require_a_durable_incident_code(self) -> SystemAlertHistoryView:
        if self.code not in _DURABLE_SYSTEM_ALERT_CODES:
            raise ValueError("System alert history only contains durable incidents.")
        return self


class SystemAlertFeedView(BaseModel):
    """The active operational conditions appropriate for one signed-in profile."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    connected: bool
    alerts: tuple[SystemAlertView, ...] = Field(default=(), max_length=10)
    history: tuple[SystemAlertHistoryView, ...] = Field(default=(), max_length=20)

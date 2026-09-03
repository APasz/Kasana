"""Strict local metadata sidecar parsing."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from kasana.katalog.limits import MAX_LIBRARY_ITEM_EXTERNAL_IDENTIFIERS
from kasana.shared.metadata import ExternalIdentifier


class LocalMetadataError(ValueError):
    """A local metadata sidecar cannot be safely applied."""


class LocalMetadata(BaseModel):
    """The supported, provider-independent fields for one local title."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=1, max_length=1_000)
    sort_title: str | None = Field(default=None, min_length=1, max_length=1_000)
    year: int | None = Field(default=None, ge=1, le=9999)
    release_date: date | None = None
    overview: str | None = Field(default=None, min_length=1, max_length=20_000)
    tags: tuple[str, ...] | None = Field(default=None, max_length=50)
    external_ids: tuple[ExternalIdentifier, ...] | None = Field(
        default=None,
        max_length=MAX_LIBRARY_ITEM_EXTERNAL_IDENTIFIERS,
    )

    @field_validator("tags")
    @classmethod
    def normalise_tags(cls, values: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if values is None:
            return None
        normalised = tuple(sorted({value.strip().casefold() for value in values}))
        if "" in normalised:
            raise ValueError("Local metadata tags must not be blank.")
        return normalised

    @field_validator("external_ids")
    @classmethod
    def require_unique_external_ids(
        cls, values: tuple[ExternalIdentifier, ...] | None
    ) -> tuple[ExternalIdentifier, ...] | None:
        if values is None:
            return None
        keys = {(value.namespace.casefold(), value.value) for value in values}
        if len(keys) != len(values):
            raise ValueError("Local metadata external IDs must be unique.")
        return values

    @model_validator(mode="after")
    def require_consistent_values(self) -> LocalMetadata:
        if not self.model_fields_set:
            raise ValueError("Local metadata must define at least one supported field.")
        for field_name in self.model_fields_set:
            if getattr(self, field_name) is None:
                raise ValueError(f"Local metadata field {field_name!r} cannot be null.")
        if self.year is not None and self.release_date is not None:
            if self.year != self.release_date.year:
                raise ValueError("Local metadata year must match release_date.")
        return self


def load_local_metadata(path: Path) -> LocalMetadata:
    """Load one UTF-8 JSON sidecar while retaining validation context."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LocalMetadataError(f"Could not parse JSON: {error}") from error
    try:
        return LocalMetadata.model_validate(payload)
    except ValidationError as error:
        raise LocalMetadataError(f"Invalid metadata: {error}") from error

"""Library view models and explicit Katalog filter mapping."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from kasana.katalog.public import Availability, LibraryItemKind, WatchedFilter


class PosterState(StrEnum):
    """Visual state rendered by a Kanvas poster."""

    NORMAL = "normal"
    IN_PROGRESS = "in_progress"
    WATCHED = "watched"
    UNAVAILABLE = "unavailable"
    SELECTED = "selected"
    LOADING = "loading"
    MISSING_ARTWORK = "missing_artwork"


class PosterView(BaseModel):
    """Safe, small poster payload for HTML and browser components."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=1_000)
    subtitle: str | None = Field(default=None, max_length=200)
    href: str = Field(pattern=r"^/item/\d+$")
    poster_url: str | None = Field(default=None, alias="posterUrl")
    progress_percent: int | None = Field(default=None, ge=0, le=100, alias="progressPercent")
    state: PosterState = PosterState.NORMAL
    available: bool


class LibraryDiagnosticCategory(StrEnum):
    """Safe development-only categories for a failed library data request."""

    INVALID_FILTERS = "invalid_filters"
    POSTER_TRANSFORMATION = "poster_transformation"
    UNEXPECTED_FAILURE = "unexpected_failure"


class LibraryPageEnvelope(BaseModel):
    """Versioned browser contract for one completed library poster page."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    items: tuple[PosterView, ...]
    next_cursor: str | None = Field(default=None, max_length=500, alias="nextCursor")
    request_id: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9_-]+$",
        alias="requestId",
    )


class LibraryErrorView(BaseModel):
    """Safe user-facing failure detail for the library browser contract."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    code: Literal["library_unavailable"] = "library_unavailable"
    message: Literal["Katalog could not load the library."] = "Katalog could not load the library."
    request_id: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9_-]+$",
        alias="requestId",
    )
    diagnostic: LibraryDiagnosticCategory | None = None


class LibraryErrorEnvelope(BaseModel):
    """Safe error wrapper returned by the library data endpoint."""

    model_config = ConfigDict(frozen=True)

    error: LibraryErrorView


class LibraryFilters(BaseModel):
    """The small first-pass filter strip, independent of query-string syntax."""

    model_config = ConfigDict(frozen=True)

    search: str | None = Field(default=None, max_length=200)
    kind: LibraryItemKind | None = None
    tags: tuple[str, ...] = ()
    watched: WatchedFilter | None = None
    availability: Availability | None = None
    year: int | None = Field(default=None, ge=1, le=9999)

    @field_validator("search")
    @classmethod
    def normalise_search(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("tags")
    @classmethod
    def normalise_tags(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        tags = tuple(sorted({value.strip().casefold() for value in values if value.strip()}))
        if len(tags) != len(values):
            raise ValueError("Tags must not be blank or repeated.")
        return tags

    def to_katalog_arguments(self) -> dict[str, object]:
        """Map visible filters to the stable public Katalog contract exactly once."""

        return {
            "kind": self.kind,
            "tags": self.tags,
            "year": self.year,
            "watched": self.watched,
            "availability": self.availability,
            "search": self.search,
        }

    @classmethod
    def from_query(
        cls, values: Mapping[str, str], *, tags: Collection[str] | None = None
    ) -> LibraryFilters:
        """Parse the intentionally small browser query surface into typed filters."""

        return cls.model_validate(
            {
                "search": values.get("search"),
                "kind": values.get("kind") or None,
                "tags": tuple(tags) if tags is not None else _query_tags(values),
                "watched": values.get("watched") or None,
                "availability": values.get("availability") or None,
                "year": values.get("year") or None,
            }
        )


def _query_tags(values: Mapping[str, str]) -> tuple[str, ...]:
    """Accept the one-value mapping used by small unit callers and API requests."""

    raw_tag = values.get("tag")
    return (raw_tag,) if raw_tag is not None else ()


@dataclass
class CursorPager:
    """Small client-equivalent state machine for cursor request coordination."""

    cursor: str | None = None
    requesting: bool = False
    exhausted: bool = False

    def begin_request(self) -> str | None:
        """Reserve the next cursor request or reject a duplicate/inapplicable request."""

        if self.requesting or self.exhausted:
            return None
        self.requesting = True
        return self.cursor

    def complete_request(self, next_cursor: str | None) -> None:
        """Accept a successful page and make a subsequent request available."""

        if not self.requesting:
            msg = "Cannot complete a cursor request that was not reserved."
            raise RuntimeError(msg)
        self.cursor = next_cursor
        self.requesting = False
        self.exhausted = next_cursor is None

    def fail_request(self) -> None:
        """Clear only the in-flight lock so an errored page can be retried safely."""

        if not self.requesting:
            msg = "Cannot fail a cursor request that was not reserved."
            raise RuntimeError(msg)
        self.requesting = False

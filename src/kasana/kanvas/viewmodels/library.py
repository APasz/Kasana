"""Library view models and explicit Katalog filter mapping."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from kasana.katalog.public import Availability, LibraryItemKind, WatchedFilter

_POSTER_HREF_PATTERN = (
    r"^/(?:item/\d+|play/item/\d+\?resume=true&onDeck=true|"
    r"play/watch-orders/\d+\?resume=true&onDeck=true)$"
)


class PlaceholderArtView(BaseModel):
    """Text payload used by generated missing-poster artwork."""

    model_config = ConfigDict(frozen=True)

    lines: tuple[str, ...] = Field(min_length=1, max_length=3)
    footer: str | None = Field(default=None, min_length=1, max_length=80)

    @field_validator("lines")
    @classmethod
    def normalise_lines(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        lines = tuple(value.strip() for value in values if value.strip())
        if len(lines) != len(values):
            raise ValueError("Placeholder lines must not be blank.")
        return lines

    @field_validator("footer")
    @classmethod
    def normalise_footer(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("Placeholder footer must not be blank.")
        return stripped


class PosterState(StrEnum):
    """Visual state rendered by a Kanvas poster."""

    NORMAL = "normal"
    IN_PROGRESS = "in_progress"
    WATCHED = "watched"
    UNAVAILABLE = "unavailable"
    SELECTED = "selected"
    LOADING = "loading"
    MISSING_ARTWORK = "missing_artwork"


class PosterAction(StrEnum):
    """Explicit launch intents available on contextual Home posters."""

    RESUME = "resume"
    PLAY_NEXT = "play_next"


class ArtworkShape(StrEnum):
    """The aspect ratio used to present a card's selected artwork."""

    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"


class PosterView(BaseModel):
    """Safe identity, artwork, context, and state for one reusable artwork card."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=1_000)
    context: str | None = Field(default=None, max_length=200)
    detail: str | None = Field(default=None, max_length=200)
    href: str = Field(pattern=_POSTER_HREF_PATTERN)
    poster_url: str | None = Field(default=None, alias="posterUrl")
    artwork_shape: ArtworkShape = Field(default=ArtworkShape.PORTRAIT, alias="artworkShape")
    mosaic_urls: tuple[str, ...] = Field(default=(), max_length=4, alias="mosaicUrls")
    placeholder: PlaceholderArtView = Field(
        default_factory=lambda: PlaceholderArtView(lines=("Untitled",))
    )
    progress_percent: int | None = Field(default=None, ge=0, le=100, alias="progressPercent")
    state: PosterState = PosterState.NORMAL
    watched: bool = False
    partially_watched: bool = Field(default=False, alias="partiallyWatched")
    available: bool

    @field_validator("context", "detail")
    @classmethod
    def normalise_optional_copy(cls, value: str | None) -> str | None:
        """Keep optional card copy compact without emitting empty visual rows."""

        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def artwork_sources_are_unambiguous(self) -> PosterView:
        """Use either one explicit poster or a collection mosaic, never both."""

        if self.poster_url is not None and self.mosaic_urls:
            raise ValueError("A poster cannot have both explicit artwork and a mosaic.")
        return self


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

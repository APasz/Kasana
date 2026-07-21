"""Application services backing Katalog's administrative CLI."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from kasana.katalog.database import KatalogDatabase
from kasana.katalog.models import (
    AuditCategory,
    AuditIssue,
    AvailabilityState,
    Kura,
    MediaFile,
    User,
    Zaisan,
    ZaisanKind,
)
from kasana.katalog.services import create_library_root, create_user

type RootKind = Literal["movie", "series"]


class AdminError(RuntimeError):
    """An expected administrative operation could not be completed."""


class KuraInput(BaseModel):
    """Validated external input for creating or changing a library root."""

    model_config = ConfigDict(extra="forbid")

    path: Path
    expected_kind: RootKind
    default_tags: tuple[str, ...] = ()
    enabled: bool = True
    display_name: str | None = Field(default=None, max_length=200)

    @field_validator("path")
    @classmethod
    def normalise_path(cls, value: Path) -> Path:
        return value.expanduser().resolve(strict=False)

    @field_validator("default_tags")
    @classmethod
    def normalise_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        tags = tuple(sorted({tag.strip() for tag in value if tag.strip()}))
        if len(tags) != len(value):
            msg = "Default tags must be non-empty after trimming."
            raise ValueError(msg)
        return tags


class KuraUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Path | None = None
    expected_kind: RootKind | None = None
    default_tags: tuple[str, ...] | None = None
    enabled: bool | None = None
    display_name: str | None = Field(default=None, max_length=200)

    @field_validator("path")
    @classmethod
    def normalise_path(cls, value: Path | None) -> Path | None:
        return value.expanduser().resolve(strict=False) if value is not None else None

    @field_validator("default_tags")
    @classmethod
    def normalise_tags(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is None:
            return None
        tags = tuple(sorted({tag.strip() for tag in value if tag.strip()}))
        if len(tags) != len(value):
            msg = "Default tags must be non-empty after trimming."
            raise ValueError(msg)
        return tags


class KuraView(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    path: Path
    expected_kind: RootKind
    default_tags: tuple[str, ...]
    enabled: bool
    display_name: str | None
    last_scan_completed_at: str | None


class UserInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=200)
    display_name: str | None = Field(default=None, max_length=200)

    @field_validator("username")
    @classmethod
    def normalise_username(cls, value: str) -> str:
        normalised = value.strip()
        if not normalised:
            raise ValueError("Username must not be blank.")
        return normalised

    @field_validator("display_name")
    @classmethod
    def normalise_display_name(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class UserView(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    username: str
    display_name: str | None


class ItemView(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    title: str
    kind: ZaisanKind
    availability: AvailabilityState
    year: int | None


class Anomaly(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    library_root_id: int
    category: AuditCategory
    path: Path
    message: str
    detected_at: str


class StatusReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    database_revision: str | None
    enabled_roots: int
    disabled_roots: int
    item_count: int
    media_file_count: int
    available_file_count: int
    unavailable_file_count: int
    unresolved_audit_issue_count: int
    roots: tuple[KuraView, ...]
    active_job_count: int = 0
    failed_job_count: int = 0


@dataclass(frozen=True)
class DatabaseAdmin:
    database_path: Path

    def initialise(self) -> str:
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            msg = f"Unable to create database directory: {error}"
            raise AdminError(msg) from error
        return self.upgrade()

    def upgrade(self) -> str:
        try:
            command.upgrade(self._alembic_config(), "head")
        except Exception as error:
            msg = f"Database migration failed: {error}"
            raise AdminError(msg) from error
        return self.current()

    def current(self) -> str:
        if not self.database_path.exists():
            msg = f"Database does not exist: {self.database_path}"
            raise AdminError(msg)
        database: KatalogDatabase | None = None
        try:
            database = KatalogDatabase(self.database_path)
            with database.engine.connect() as connection:
                revision = MigrationContext.configure(connection).get_current_revision()
        except (OSError, SQLAlchemyError, ValueError) as error:
            msg = f"Unable to inspect database revision: {error}"
            raise AdminError(msg) from error
        finally:
            if database is not None:
                database.close()
        if revision is None:
            msg = "Database has no Alembic revision."
            raise AdminError(msg)
        return revision

    def _alembic_config(self) -> Config:
        repository_root = Path(__file__).parents[3]
        config = Config(str(repository_root / "alembic.ini"))
        config.set_main_option("script_location", str(repository_root / "alembic"))
        config.set_main_option("sqlalchemy.url", f"sqlite:///{self.database_path}")
        return config


class KatalogAdmin:
    """Focused root, audit, and status operations for Katalog administrators."""

    def __init__(self, database: KatalogDatabase) -> None:
        self.database = database

    def list_roots(self) -> tuple[KuraView, ...]:
        return self.database.run_transaction(
            lambda session: tuple(
                _root_view(root) for root in session.scalars(select(Kura).order_by(Kura.id)).all()
            )
        )

    def list_users(self) -> tuple[UserView, ...]:
        return self.database.run_transaction(
            lambda session: tuple(
                _user_view(user) for user in session.scalars(select(User).order_by(User.id)).all()
            )
        )

    def create_user(self, user_input: UserInput) -> UserView:
        try:
            return self.database.run_transaction(
                lambda session: _user_view(
                    create_user(
                        session,
                        username=user_input.username,
                        display_name=user_input.display_name,
                    )
                )
            )
        except IntegrityError as error:
            msg = f"A user already uses username {user_input.username}."
            raise AdminError(msg) from error

    def search_items(
        self,
        query: str,
        *,
        limit: int = 20,
        year: int | None = None,
        kind: ZaisanKind | None = None,
    ) -> tuple[ItemView, ...]:
        normalised_query = query.strip()
        if not normalised_query:
            raise AdminError("Item search text must not be blank.")
        if not 1 <= limit <= 100:
            raise AdminError("Item search limit must be between 1 and 100.")
        if year is not None and not 1 <= year <= 9999:
            raise AdminError("Item search year must be between 1 and 9999.")
        needle = normalised_query.casefold()

        def load(session: Session) -> tuple[ItemView, ...]:
            statement = select(Zaisan).where(func.lower(Zaisan.title).contains(needle))
            if year is not None:
                statement = statement.where(Zaisan.release_year == year)
            if kind is not None:
                statement = statement.where(Zaisan.item_kind == kind)
            candidates = tuple(session.scalars(statement))
            ranked = sorted(candidates, key=lambda item: _item_search_key(item, needle))
            return tuple(_item_view(item) for item in ranked[:limit])

        return self.database.run_transaction(load)

    def get_item(self, item_id: int) -> ItemView:
        def load(session: Session) -> ItemView:
            item = session.get(Zaisan, item_id)
            if item is None:
                raise AdminError(f"Library item {item_id} does not exist.")
            return _item_view(item)

        return self.database.run_transaction(load)

    def add_root(self, root_input: KuraInput) -> KuraView:
        try:
            return self.database.run_transaction(
                lambda session: _root_view(
                    create_library_root(
                        session,
                        path=root_input.path,
                        expected_media_kind=ZaisanKind(root_input.expected_kind),
                        default_tags=frozenset(root_input.default_tags),
                        enabled=root_input.enabled,
                        display_name=root_input.display_name,
                    )
                )
            )
        except IntegrityError as error:
            msg = f"A library root already uses path {root_input.path}."
            raise AdminError(msg) from error

    def update_root(self, root_id: int, changes: KuraUpdate) -> KuraView:
        try:
            return self.database.run_transaction(
                lambda session: self._update_root(session, root_id, changes)
            )
        except IntegrityError as error:
            msg = "Another library root already uses that path."
            raise AdminError(msg) from error

    def remove_root(self, root_id: int) -> None:
        def remove(session: Session) -> None:
            root = _require_root(session, root_id)
            session.delete(root)
            session.flush()

        self.database.run_transaction(remove)

    def list_audit_issues(
        self, *, root_id: int | None = None, category: AuditCategory | None = None
    ) -> tuple[Anomaly, ...]:
        def load(session: Session) -> tuple[Anomaly, ...]:
            statement = select(AuditIssue).where(AuditIssue.is_resolved.is_(False))
            if root_id is not None:
                statement = statement.where(AuditIssue.library_root_id == root_id)
            if category is not None:
                statement = statement.where(AuditIssue.category == category)
            issues = session.scalars(
                statement.order_by(AuditIssue.library_root_id, AuditIssue.id)
            ).all()
            return tuple(_issue_view(issue) for issue in issues)

        return self.database.run_transaction(load)

    def status(self, database_revision: str) -> StatusReport:
        def load(session: Session) -> StatusReport:
            roots = tuple(session.scalars(select(Kura).order_by(Kura.id)).all())
            enabled_roots = sum(root.enabled for root in roots)
            media_file_count = _count(session, select(func.count()).select_from(MediaFile))
            return StatusReport(
                database_revision=database_revision,
                enabled_roots=enabled_roots,
                disabled_roots=len(roots) - enabled_roots,
                item_count=_count(session, select(func.count()).select_from(Zaisan)),
                media_file_count=media_file_count,
                available_file_count=_count(
                    session,
                    select(func.count()).where(
                        MediaFile.availability == AvailabilityState.AVAILABLE
                    ),
                ),
                unavailable_file_count=_count(
                    session,
                    select(func.count()).where(
                        MediaFile.availability == AvailabilityState.UNAVAILABLE
                    ),
                ),
                unresolved_audit_issue_count=_count(
                    session,
                    select(func.count()).where(AuditIssue.is_resolved.is_(False)),
                ),
                roots=tuple(_root_view(root) for root in roots),
            )

        return self.database.run_transaction(load)

    @staticmethod
    def _update_root(session: Session, root_id: int, changes: KuraUpdate) -> KuraView:
        root = _require_root(session, root_id)
        if changes.path is not None:
            root.path = str(changes.path)
        if changes.expected_kind is not None:
            root.expected_media_kind = ZaisanKind(changes.expected_kind)
        if changes.default_tags is not None:
            root.default_tags = list(changes.default_tags)
        if changes.enabled is not None:
            root.enabled = changes.enabled
        if changes.display_name is not None:
            root.display_name = changes.display_name.strip() or None
        session.flush()
        return _root_view(root)


def _require_root(session: Session, root_id: int) -> Kura:
    root = session.get(Kura, root_id)
    if root is None:
        msg = f"Library root {root_id} does not exist."
        raise AdminError(msg)
    return root


def _root_view(root: Kura) -> KuraView:
    match root.expected_media_kind:
        case ZaisanKind.MOVIE:
            expected_kind: RootKind = "movie"
        case ZaisanKind.SERIES:
            expected_kind = "series"
        case _:
            msg = (
                f"Library root {root.id} has unsupported expected kind {root.expected_media_kind}."
            )
            raise AdminError(msg)
    return KuraView(
        id=root.id,
        path=Path(root.path),
        expected_kind=expected_kind,
        default_tags=tuple(root.default_tags),
        enabled=root.enabled,
        display_name=root.display_name,
        last_scan_completed_at=root.last_scan_completed_at.isoformat()
        if root.last_scan_completed_at is not None
        else None,
    )


def _user_view(user: User) -> UserView:
    return UserView(id=user.id, username=user.username, display_name=user.display_name)


def _item_view(item: Zaisan) -> ItemView:
    return ItemView(
        id=item.id,
        title=item.title,
        kind=item.item_kind,
        availability=item.availability,
        year=item.release_year,
    )


def _item_search_key(item: Zaisan, needle: str) -> tuple[int, str, int]:
    title = item.title.casefold()
    if title == needle:
        rank = 0
    elif re.match(rf"^{re.escape(needle)}(?!\w)", title) is not None:
        rank = 1
    elif re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", title) is not None:
        rank = 2
    elif title.startswith(needle):
        rank = 3
    else:
        rank = 4
    return rank, item.sort_title.casefold(), item.id


def _issue_view(issue: AuditIssue) -> Anomaly:
    return Anomaly(
        id=issue.id,
        library_root_id=issue.library_root_id,
        category=issue.category,
        path=Path(issue.path),
        message=issue.message,
        detected_at=issue.detected_at.isoformat(),
    )


def _count(session: Session, statement: Select[tuple[int]]) -> int:
    return session.scalar(statement) or 0

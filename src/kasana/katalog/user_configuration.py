"""Filesystem source of truth for local Kanvas profile configuration."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from tempfile import NamedTemporaryFile

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from kasana.configuration import user_configuration_directory
from kasana.katalog.models import User, UserRole
from kasana.shared.profile_rules import (
    PROFILE_ACCENT_COLOUR_DEFAULT,
    PROFILE_ACCENT_COLOUR_PATTERN,
    PROFILE_PIN_MAX_LENGTH,
    PROFILE_PIN_MIN_LENGTH,
)

_CONFIGURATION_FILENAME = "configuration.json"


class UserConfigurationState(StrEnum):
    """Whether a configured profile may start a Kanvas or playback session."""

    ACTIVE = "active"
    DISABLED = "disabled"


class UserConfiguration(BaseModel):
    """The complete, non-library configuration for one numeric user directory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    username: str = Field(min_length=1, max_length=200)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    level: UserRole = UserRole.USER
    state: UserConfigurationState = UserConfigurationState.ACTIVE
    pin: str | None = Field(
        default=None, min_length=PROFILE_PIN_MIN_LENGTH, max_length=PROFILE_PIN_MAX_LENGTH
    )
    accent_colour: str = Field(
        default=PROFILE_ACCENT_COLOUR_DEFAULT, pattern=PROFILE_ACCENT_COLOUR_PATTERN
    )

    @field_validator("username", "name")
    @classmethod
    def normalise_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalised = value.strip()
        if not normalised:
            raise ValueError("User configuration text must not be blank.")
        return normalised


class UserConfigurationStore:
    """Stores profiles as ``<users>/<id>/configuration.json`` documents."""

    def __init__(self, directory: Path | None = None) -> None:
        self._directory = directory or user_configuration_directory()

    def load(self, user_id: int) -> UserConfiguration:
        path = self._configuration_path(user_id)
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise ValueError(f"User configuration for ID {user_id} does not exist.") from error
        except json.JSONDecodeError as error:
            raise ValueError(f"Invalid JSON in user configuration {path}.") from error
        if not isinstance(document, dict):
            raise ValueError(f"User configuration {path} must be an object.")
        return UserConfiguration.model_validate(document)

    def load_or_migrate(self, user: User) -> UserConfiguration:
        """Create a one-time filesystem projection for a legacy SQLite profile."""

        try:
            return self.load(user.id)
        except ValueError as error:
            if self._configuration_path(user.id).is_file():
                raise error
        configuration = UserConfiguration(
            username=user.username,
            name=user.display_name,
            level=UserRole(user.role.value),
            state=(
                UserConfigurationState.DISABLED
                if user.is_disabled
                else UserConfigurationState.ACTIVE
            ),
            pin=user.pin,
        )
        self.save(user.id, configuration)
        return configuration

    def save(self, user_id: int, configuration: UserConfiguration) -> None:
        path = self._configuration_path(user_id)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = json.dumps(configuration.model_dump(mode="json"), indent=4, sort_keys=True) + "\n"
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".configuration-", delete=False
        ) as temporary_file:
            temporary_file.write(payload)
            temporary_path = Path(temporary_file.name)
        temporary_path.chmod(0o600)
        temporary_path.replace(path)

    def configuration_exists(self, user_id: int) -> bool:
        return self._configuration_path(user_id).is_file()

    def configured_users(self) -> tuple[tuple[int, UserConfiguration], ...]:
        """Return every valid numeric user directory in stable identifier order."""

        if not self._directory.is_dir():
            return ()
        configured_users: list[tuple[int, UserConfiguration]] = []
        for directory in self._directory.iterdir():
            if not directory.is_dir() or not directory.name.isdecimal():
                continue
            user_id = int(directory.name)
            if user_id <= 0:
                continue
            configured_users.append((user_id, self.load(user_id)))
        return tuple(sorted(configured_users, key=lambda entry: entry[0]))

    def synchronise_database_users(self, session: Session) -> None:
        """Project configured IDs into SQLite rows needed by playback foreign keys."""

        configured_usernames: set[str] = set()
        for user_id, configuration in self.configured_users():
            if configuration.username in configured_usernames:
                raise ValueError("User configuration usernames must be unique.")
            configured_usernames.add(configuration.username)
            user = session.get(User, user_id)
            if user is None:
                user = User(id=user_id)
                session.add(user)
            user.username = configuration.username
            user.display_name = configuration.name
            user.role = UserRole(configuration.level.value)
            user.is_disabled = configuration.state is UserConfigurationState.DISABLED
            user.pin = configuration.pin
        session.flush()

    def _configuration_path(self, user_id: int) -> Path:
        if user_id <= 0:
            raise ValueError("User IDs must be positive.")
        return self._directory / str(user_id) / _CONFIGURATION_FILENAME

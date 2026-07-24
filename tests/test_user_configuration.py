"""Migration contracts for filesystem-backed user configuration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from kasana.katalog.models import User, UserRole
from kasana.katalog.user_configuration import UserConfigurationStore


def test_legacy_integer_pin_migrates_to_the_valid_string_configuration_form(tmp_path: Path) -> None:
    user = User(id=7, username="owner", role=UserRole.USER, pin=cast(str, 501))
    store = UserConfigurationStore(tmp_path / "users")

    configuration = store.load_or_migrate(user)

    assert configuration.pin == "501"
    assert user.pin is None
    assert store.load(7).pin == "501"


def test_legacy_integer_pin_in_an_existing_configuration_file_migrates_on_load(
    tmp_path: Path,
) -> None:
    store = UserConfigurationStore(tmp_path / "users")
    configuration_path = tmp_path / "users" / "7" / "configuration.json"
    configuration_path.parent.mkdir(parents=True)
    configuration_path.write_text(
        json.dumps({"username": "owner", "level": "user", "pin": 501}), encoding="utf-8"
    )

    configuration = store.load(7)

    assert configuration.pin == "501"
    assert json.loads(configuration_path.read_text(encoding="utf-8"))["pin"] == "501"

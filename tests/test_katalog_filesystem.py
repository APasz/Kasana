"""Filesystem capability probe contracts used by Katalog root health checks."""

from __future__ import annotations

from pathlib import Path
from typing import Never

import pytest

from kasana.katalog.filesystem import (
    is_accessible_directory,
    is_library_root_accessible,
    is_mounted_directory,
)


def test_accessible_directory_requires_readable_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()

    def raise_permission_error(_path: object) -> Never:
        raise PermissionError("filesystem access denied")

    monkeypatch.setattr("kasana.katalog.filesystem.os.scandir", raise_permission_error)

    assert not is_accessible_directory(library_root)


@pytest.mark.parametrize("error_type", (RuntimeError, ValueError))
def test_mounted_directory_treats_invalid_filesystem_values_as_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error_type: type[Exception]
) -> None:
    def raise_filesystem_error(_path: Path) -> Never:
        raise error_type("invalid filesystem path")

    monkeypatch.setattr(Path, "is_mount", raise_filesystem_error)

    assert not is_mounted_directory(tmp_path)


def test_required_mount_paths_distinguish_a_mount_from_a_regular_directory(tmp_path: Path) -> None:
    mount_path = tmp_path / "SabaWolf"
    library_root = mount_path / "Movies"
    library_root.mkdir(parents=True)

    assert not is_library_root_accessible(
        library_root,
        required_mount_path=mount_path,
    )

"""Filesystem capability probes shared by library health checks and scanning."""

from __future__ import annotations

import os
from pathlib import Path


def is_accessible_directory(path: Path) -> bool:
    """Return whether this process can search and read a directory's entries."""

    try:
        if not path.is_dir() or not os.access(path, os.R_OK | os.X_OK):
            return False
        with os.scandir(path) as entries:
            next(entries, None)
    except OSError, RuntimeError, ValueError:
        return False
    return True


def is_library_root_accessible(path: Path, *, required_mount_path: Path | None) -> bool:
    """Return whether a root is readable and its optional storage mount is present."""

    return (
        required_mount_path is None or is_mounted_directory(required_mount_path)
    ) and is_accessible_directory(path)


def is_mounted_directory(path: Path) -> bool:
    """Return whether ``path`` is a filesystem mount point for this process."""

    try:
        return path.is_mount()
    except OSError, RuntimeError, ValueError:
        return False

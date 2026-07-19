"""Browser-level contracts for the native Kanvas library grid."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_library_grid_renders_or_shows_a_categorised_retry_state() -> None:
    """Exercise the browser bundle without accepting a blank or generic grid failure."""

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the browser library grid contract.")
    repository_root = Path(__file__).parents[1]
    result = subprocess.run(
        [node, "tests/browser_library_grid_runner.js"],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "browser library grid checks passed\n"

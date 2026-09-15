"""Regression checks for episode grouping and range movement in the browser."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_episode_ranges_preserve_the_complete_explicit_sequence() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the watch-order browser contract.")
    result = subprocess.run(
        [node, "tests/browser_watch_orders_runner.js"],
        cwd=Path(__file__).parents[1],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "browser watch-order checks passed\n"

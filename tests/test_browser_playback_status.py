"""Browser-level status lifecycle checks for the custom playback player."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_select_play_status_clears_after_playback_starts() -> None:
    """Keep an autoplay hint from outliving a successful manual play request."""

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the browser playback status contract.")
    repository_root = Path(__file__).parents[1]
    result = subprocess.run(
        [node, "tests/browser_playback_status_runner.js"],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "browser playback status checks passed\n"

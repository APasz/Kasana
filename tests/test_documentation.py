"""Regression checks for the supported playback path described to users."""

from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_playback_documentation_keeps_kanvas_primary_and_kestrel_optional() -> None:
    readme = (_PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    architecture = (_PROJECT_ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")

    assert "Kanvas is the primary player." in readme
    assert "Kestrel nor mpv is required" in readme
    assert "Kanvas is the primary player" in architecture
    assert "Kestrel is an optional local mpv fallback" in architecture
    assert "VLC" not in architecture

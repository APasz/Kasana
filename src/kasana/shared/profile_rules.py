"""Shared trusted-LAN profile gate rules used by Katalog and Kanvas.

Profile PINs are intentionally plaintext convenience gates, not passwords or
network authentication. ``UserConfigurationStore`` is their sole source of
truth; callers must never return or log a PIN.
"""

from __future__ import annotations

from typing import Final

PROFILE_ACCENT_COLOUR_DEFAULT: Final = "#e8e8e8"
PROFILE_ACCENT_COLOUR_PATTERN: Final = r"^#[0-9A-Fa-f]{6}$"
PROFILE_PIN_MIN_LENGTH: Final = 2
PROFILE_PIN_MAX_LENGTH: Final = 16

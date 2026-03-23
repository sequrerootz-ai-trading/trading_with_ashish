from __future__ import annotations

MODE = "PAPER"  # or "LIVE"
VALID_MODES = {"PAPER", "LIVE"}


def get_mode() -> str:
    mode = MODE.strip().upper()
    if mode not in VALID_MODES:
        raise Exception("Invalid MODE")
    return mode

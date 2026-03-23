from __future__ import annotations

RESET = "[0m"
BOLD = "[1m"
YELLOW = "[93m"
GREEN = "[92m"
RED = "[91m"
CYAN = "[96m"


def colorize(text: str, color: str, bold: bool = False) -> str:
    prefix = color
    if bold:
        prefix += BOLD
    return f"{prefix}{text}{RESET}"

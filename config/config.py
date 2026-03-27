from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
load_dotenv(ENV_FILE, override=False)

MODE = "PAPER"  # or "LIVE"
VALID_MODES = {"PAPER", "LIVE"}
VALID_MARKET_TYPES = {"EQUITY", "MCX"}


def get_mode() -> str:
    mode = MODE.strip().upper()
    if mode not in VALID_MODES:
        raise Exception("Invalid MODE")
    return mode


def get_symbol() -> str:
    symbol = os.getenv("SYMBOL", "").strip().upper()
    if not symbol:
        raise ValueError("Missing SYMBOL in .env")
    return symbol


def get_market_type() -> str:
    market_type = os.getenv("MARKET_TYPE", "EQUITY").strip().upper()
    if market_type not in VALID_MARKET_TYPES:
        raise ValueError(
            f"Invalid MARKET_TYPE in .env: {market_type}. Expected one of {sorted(VALID_MARKET_TYPES)}"
        )
    return market_type

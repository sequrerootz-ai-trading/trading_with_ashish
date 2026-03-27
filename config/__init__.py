"""Configuration package for the algo trading project."""

from config.config import (
    MODE,
    VALID_MARKET_TYPES,
    VALID_MODES,
    get_market_type,
    get_mode,
    get_symbol,
)

__all__ = [
    "MODE",
    "VALID_MARKET_TYPES",
    "VALID_MODES",
    "get_market_type",
    "get_mode",
    "get_symbol",
]

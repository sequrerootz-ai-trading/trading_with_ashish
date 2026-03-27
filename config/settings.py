from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from config.config import get_market_type, get_symbol


BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
load_dotenv(ENV_FILE)


@dataclass(frozen=True)
class InstrumentConfig:
    label: str
    market_type: str
    exchange: str
    tradingsymbol: str
    segment: str = "CASH"
    tick_size: float = 0.05
    lot_size: int = 1
    supports_options: bool = False


@dataclass(frozen=True)
class ExecutionSettings:
    capital_per_trade: float = 2000.0
    stop_loss_percent: float = 0.20
    trailing_stop_loss_percent: float = 0.20
    max_retries: int = 5
    retry_delay_seconds: float = 1.0
    poll_attempts: int = 10
    poll_interval_seconds: float = 1.0
    default_product: str = "MIS"


@dataclass(frozen=True)
class Settings:
    api_key: str
    access_token: str
    symbol: str
    market_type: str
    candle_interval_minutes: int = 3
    max_candles_in_memory: int = 200
    execution: ExecutionSettings = field(default_factory=ExecutionSettings)
    instruments: list[InstrumentConfig] = field(default_factory=list)


EQUITY_PRESET_MAP: dict[str, InstrumentConfig] = {
    "NIFTY": InstrumentConfig(
        label="NIFTY",
        market_type="EQUITY",
        exchange="NSE",
        tradingsymbol="NIFTY 50",
        segment="INDEX",
        supports_options=True,
    ),
    "BANKNIFTY": InstrumentConfig(
        label="BANKNIFTY",
        market_type="EQUITY",
        exchange="NSE",
        tradingsymbol="NIFTY BANK",
        segment="INDEX",
        supports_options=True,
    ),
    "SENSEX": InstrumentConfig(
        label="SENSEX",
        market_type="EQUITY",
        exchange="BSE",
        tradingsymbol="SENSEX",
        segment="INDEX",
        supports_options=True,
    ),
}

MCX_SYMBOL_MAP: dict[str, InstrumentConfig] = {
    "CRUDEOIL": InstrumentConfig(
        label="CRUDEOIL",
        market_type="MCX",
        exchange="MCX",
        tradingsymbol="CRUDEOIL",
        segment="FUT",
        tick_size=1.0,
        lot_size=100,
    ),
    "NATURALGAS": InstrumentConfig(
        label="NATURALGAS",
        market_type="MCX",
        exchange="MCX",
        tradingsymbol="NATURALGAS",
        segment="FUT",
        tick_size=0.1,
        lot_size=1250,
    ),
    "GOLD": InstrumentConfig(
        label="GOLD",
        market_type="MCX",
        exchange="MCX",
        tradingsymbol="GOLD",
        segment="FUT",
        tick_size=1.0,
        lot_size=1,
    ),
}

SYMBOL_MAP: dict[str, dict[str, InstrumentConfig]] = {
    "EQUITY": EQUITY_PRESET_MAP,
    "MCX": MCX_SYMBOL_MAP,
}


def get_settings() -> Settings:
    api_key = os.getenv("KITE_API_KEY", "").strip()
    access_token = os.getenv("KITE_ACCESS_TOKEN", "").strip()
    symbol = get_symbol()
    market_type = get_market_type()

    if not api_key or not access_token:
        raise ValueError(
            "Missing Kite credentials. Set KITE_API_KEY and KITE_ACCESS_TOKEN in .env."
        )

    execution_settings = ExecutionSettings(
        capital_per_trade=float(os.getenv("CAPITAL_PER_TRADE", "2000")),
        stop_loss_percent=float(os.getenv("STOP_LOSS_PERCENT", "0.20")),
        trailing_stop_loss_percent=float(os.getenv("TRAILING_STOP_LOSS_PERCENT", "0.20")),
        max_retries=int(os.getenv("ORDER_MAX_RETRIES", "5")),
        retry_delay_seconds=float(os.getenv("ORDER_RETRY_DELAY_SECONDS", "1")),
        poll_attempts=int(os.getenv("ORDER_STATUS_POLL_ATTEMPTS", "10")),
        poll_interval_seconds=float(os.getenv("ORDER_STATUS_POLL_INTERVAL_SECONDS", "1")),
        default_product=os.getenv("ORDER_PRODUCT", "MIS").strip().upper() or "MIS",
    )

    if market_type == "EQUITY":
        instrument = _build_equity_instrument(symbol)
    else:
        available_symbols = SYMBOL_MAP.get(market_type, {})
        if symbol not in available_symbols:
            raise ValueError(
                f"Invalid SYMBOL in .env for MARKET_TYPE={market_type}: {symbol}. "
                f"Supported symbols: {sorted(available_symbols)}"
            )
        instrument = available_symbols[symbol]

    return Settings(
        api_key=api_key,
        access_token=access_token,
        symbol=symbol,
        market_type=market_type,
        candle_interval_minutes=int(os.getenv("CANDLE_INTERVAL_MINUTES", "3")),
        execution=execution_settings,
        instruments=[instrument],
    )


def _build_equity_instrument(symbol: str) -> InstrumentConfig:
    normalized_symbol = _normalize_equity_symbol(symbol)
    preset = EQUITY_PRESET_MAP.get(normalized_symbol)
    if preset is not None:
        return preset

    default_exchange = "BSE" if normalized_symbol in {"BANKEX"} else "AUTO"
    default_segment = "INDEX" if normalized_symbol in {"BANKEX"} else "AUTO"
    return InstrumentConfig(
        label=normalized_symbol,
        market_type="EQUITY",
        exchange=default_exchange,
        tradingsymbol=normalized_symbol,
        segment=default_segment,
        supports_options=True,
    )


def _normalize_equity_symbol(symbol: str) -> str:
    value = symbol.strip().upper()
    aliases = {
        "NIFTY 50": "NIFTY",
        "NIFTY BANK": "BANKNIFTY",
        "BANK NIFTY": "BANKNIFTY",
    }
    return aliases.get(value, value)

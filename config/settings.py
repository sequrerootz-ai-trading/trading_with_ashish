from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
load_dotenv(ENV_FILE)


@dataclass(frozen=True)
class InstrumentConfig:
    label: str
    exchange: str
    tradingsymbol: str


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
    candle_interval_minutes: int = 5
    max_candles_in_memory: int = 200
    execution: ExecutionSettings = field(default_factory=ExecutionSettings)
    instruments: list[InstrumentConfig] = field(default_factory=list)


SYMBOL_MAP = {
    "NIFTY": InstrumentConfig(label="NIFTY", exchange="NSE", tradingsymbol="NIFTY 50"),
    "BANKNIFTY": InstrumentConfig(label="BANKNIFTY", exchange="NSE", tradingsymbol="NIFTY BANK"),
    "RELIANCE": InstrumentConfig(label="RELIANCE", exchange="NSE", tradingsymbol="RELIANCE"),
    "SBIN": InstrumentConfig(label="SBIN", exchange="NSE", tradingsymbol="SBIN"),
    "PETRONET": InstrumentConfig(label="PETRONET", exchange="NSE", tradingsymbol="PETRONET"),
}


def get_settings() -> Settings:
    api_key = os.getenv("KITE_API_KEY", "").strip()
    access_token = os.getenv("KITE_ACCESS_TOKEN", "").strip()
    symbol = os.getenv("SYMBOL", "").strip().upper()

    if not symbol:
        raise ValueError("Missing SYMBOL in .env. Example: SYMBOL=PETRONET")
    if symbol not in SYMBOL_MAP:
        raise ValueError(f"Invalid SYMBOL in .env: {symbol}")
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

    instrument = SYMBOL_MAP[symbol]
    return Settings(
        api_key=api_key,
        access_token=access_token,
        symbol=symbol,
        execution=execution_settings,
        instruments=[instrument],
    )

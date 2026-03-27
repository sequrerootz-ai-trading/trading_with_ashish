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
    account_balance: float = 200000.0
    risk_per_trade_pct: float = 0.01
    max_capital_exposure: float = 50000.0
    stop_loss_percent: float = 0.20
    trailing_stop_loss_percent: float = 0.20
    max_retries: int = 5
    retry_delay_seconds: float = 1.0
    poll_attempts: int = 10
    poll_interval_seconds: float = 1.0
    default_product: str = "MIS"
    min_premium: float = 80.0
    max_premium: float = 300.0
    adx_trending_threshold: float = 25.0
    adx_sideways_threshold: float = 20.0
    atr_spike_multiplier: float = 1.5
    range_compression_threshold_pct: float = 0.012
    atr_stop_multiplier: float = 1.5
    fixed_trail_step_pct: float = 0.08
    volume_spike_multiplier: float = 1.5
    max_daily_loss: float = 2000.0
    max_drawdown: float = 5000.0
    max_trades_per_day: int = 5
    cooldown_after_two_losses: int = 5
    stop_after_three_losses: int = 3
    partial_profit_rr: float = 1.0
    time_exit_minutes: int = 30
    no_movement_threshold_pct: float = 0.05
    slippage_entry_factor: float = 1.01
    slippage_entry_factor_high: float = 1.02
    weekly_expiry_cutoff_hour: int = 13
    kill_switch: bool = False
    trading_window_morning_start: str = "09:30"
    trading_window_morning_end: str = "11:30"
    trading_window_afternoon_start: str = "13:30"
    trading_window_afternoon_end: str = "14:30"
    stop_loss_mode: str = "ATR"
    trailing_mode: str = "PREVIOUS_CANDLE"


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
        capital_per_trade=_get_env_float("CAPITAL_PER_TRADE", 2000.0),
        account_balance=_get_env_float("ACCOUNT_BALANCE", 200000.0),
        risk_per_trade_pct=_get_env_float("RISK_PER_TRADE_PCT", 0.01),
        max_capital_exposure=_get_env_float("MAX_CAPITAL_EXPOSURE", 50000.0),
        stop_loss_percent=_get_env_float("STOP_LOSS_PERCENT", 0.20),
        trailing_stop_loss_percent=_get_env_float("TRAILING_STOP_LOSS_PERCENT", 0.20),
        max_retries=int(os.getenv("ORDER_MAX_RETRIES", "5")),
        retry_delay_seconds=_get_env_float("ORDER_RETRY_DELAY_SECONDS", 1.0),
        poll_attempts=int(os.getenv("ORDER_STATUS_POLL_ATTEMPTS", "10")),
        poll_interval_seconds=_get_env_float("ORDER_STATUS_POLL_INTERVAL_SECONDS", 1.0),
        default_product=os.getenv("ORDER_PRODUCT", "MIS").strip().upper() or "MIS",
        min_premium=_get_env_float("MIN_PREMIUM", 80.0),
        max_premium=_get_env_float("MAX_PREMIUM", 300.0),
        adx_trending_threshold=_get_env_float("ADX_TRENDING_THRESHOLD", 25.0),
        adx_sideways_threshold=_get_env_float("ADX_SIDEWAYS_THRESHOLD", 20.0),
        atr_spike_multiplier=_get_env_float("ATR_SPIKE_MULTIPLIER", 1.5),
        range_compression_threshold_pct=_get_env_float("RANGE_COMPRESSION_THRESHOLD_PCT", 0.012),
        atr_stop_multiplier=_get_env_float("ATR_STOP_MULTIPLIER", 1.5),
        fixed_trail_step_pct=_get_env_float("FIXED_TRAIL_STEP_PCT", 0.08),
        volume_spike_multiplier=_get_env_float("VOLUME_SPIKE_MULTIPLIER", 1.5),
        max_daily_loss=_get_env_float("MAX_DAILY_LOSS", 2000.0),
        max_drawdown=_get_env_float("MAX_DRAWDOWN", 5000.0),
        max_trades_per_day=int(os.getenv("MAX_TRADES_PER_DAY", "5")),
        cooldown_after_two_losses=int(os.getenv("COOLDOWN_AFTER_TWO_LOSSES", "5")),
        stop_after_three_losses=int(os.getenv("STOP_AFTER_THREE_LOSSES", "3")),
        partial_profit_rr=_get_env_float("PARTIAL_PROFIT_RR", 1.0),
        time_exit_minutes=int(os.getenv("TIME_EXIT_MINUTES", "30")),
        no_movement_threshold_pct=_get_env_float("NO_MOVEMENT_THRESHOLD_PCT", 0.05),
        slippage_entry_factor=_get_env_float("SLIPPAGE_ENTRY_FACTOR", 1.01),
        slippage_entry_factor_high=_get_env_float("SLIPPAGE_ENTRY_FACTOR_HIGH", 1.02),
        weekly_expiry_cutoff_hour=int(os.getenv("WEEKLY_EXPIRY_CUTOFF_HOUR", "13")),
        kill_switch=_get_env_bool("KILL_SWITCH", False),
        trading_window_morning_start=os.getenv("TRADING_WINDOW_MORNING_START", "09:30").strip(),
        trading_window_morning_end=os.getenv("TRADING_WINDOW_MORNING_END", "11:30").strip(),
        trading_window_afternoon_start=os.getenv("TRADING_WINDOW_AFTERNOON_START", "13:30").strip(),
        trading_window_afternoon_end=os.getenv("TRADING_WINDOW_AFTERNOON_END", "14:30").strip(),
        stop_loss_mode=os.getenv("STOP_LOSS_MODE", "ATR").strip().upper() or "ATR",
        trailing_mode=os.getenv("TRAILING_MODE", "PREVIOUS_CANDLE").strip().upper() or "PREVIOUS_CANDLE",
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


def _get_env_float(name: str, default: float) -> float:
    raw_value = os.getenv(name, str(default)).strip()
    cleaned_value = "".join(ch for ch in raw_value if ch.isdigit() or ch in {".", "-"})
    if cleaned_value in {"", ".", "-", "-."}:
        raise ValueError(f"Invalid float value for {name}: {raw_value}")
    return float(cleaned_value)


def _get_env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

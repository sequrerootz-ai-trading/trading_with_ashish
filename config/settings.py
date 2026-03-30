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
    partial_profit_size_pct: float = 0.50
    time_exit_minutes: int = 30
    no_movement_threshold_pct: float = 0.05
    hard_flatten_minutes_before_close: int = 20
    slippage_entry_factor: float = 1.01
    slippage_entry_factor_high: float = 1.02
    max_entry_slippage_pct: float = 0.03
    weekly_expiry_cutoff_hour: int = 13
    kill_switch: bool = False
    trading_window_morning_start: str = "09:30"
    trading_window_morning_end: str = "11:30"
    trading_window_afternoon_start: str = "13:30"
    trading_window_afternoon_end: str = "14:30"
    stop_loss_mode: str = "ATR"
    trailing_mode: str = "PREVIOUS_CANDLE"
    min_stop_loss_pct: float = 0.10
    max_stop_loss_pct: float = 0.35
    trailing_rr_lock_step: float = 0.75
    trailing_buffer_pct: float = 0.12
    drawdown_size_reduction_start_pct: float = 0.5
    drawdown_size_reduction_factor: float = 0.5
    enable_vwap_filter: bool = True
    enable_volume_filter: bool = True
    enable_volatility_filter: bool = True
    enable_higher_timeframe_trend_filter: bool = True
    higher_timeframe_multiple: int = 5
    higher_timeframe_fast_ema: int = 9
    higher_timeframe_slow_ema: int = 21
    filter_score_threshold: float = 2.6
    filter_score_trending_bonus: float = 0.5
    filter_score_volatile_bonus: float = 0.35
    filter_score_sideways_penalty: float = 0.25
    filter_confidence_bonus: float = 0.75
    filter_sideways_weight: float = 1.0
    filter_ema_weight: float = 0.9
    filter_volatility_weight: float = 0.9
    filter_vwap_weight: float = 0.8
    filter_volume_weight: float = 0.8
    filter_higher_tf_weight: float = 0.7
    max_filter_penalty_cap: float = 3.0


@dataclass(frozen=True)
class Settings:
    api_key: str
    access_token: str
    symbol: str
    market_type: str
    execution_profile: str = "STANDARD"
    candle_interval_minutes: int = 3
    max_candles_in_memory: int = 200
    stale_tick_warning_seconds: int = 45
    stale_candle_warning_seconds: int = 300
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


# NEW: optional profile defaults. Explicit env vars still override these values.
EXECUTION_PROFILE_DEFAULTS: dict[str, dict[str, float | int | bool | str]] = {
    "STANDARD": {
        "capital_per_trade": 2000.0,
        "risk_per_trade_pct": 0.01,
        "max_capital_exposure": 50000.0,
        "max_premium": 300.0,
        "volume_spike_multiplier": 1.5,
        "max_daily_loss": 2000.0,
        "max_drawdown": 5000.0,
        "max_trades_per_day": 5,
        "cooldown_after_two_losses": 5,
        "partial_profit_rr": 1.0,
        "partial_profit_size_pct": 0.50,
        "time_exit_minutes": 30,
        "slippage_entry_factor": 1.01,
        "slippage_entry_factor_high": 1.02,
        "max_entry_slippage_pct": 0.03,
        "min_stop_loss_pct": 0.10,
        "max_stop_loss_pct": 0.35,
        "trailing_buffer_pct": 0.12,
        "filter_score_threshold": 2.6,
        "filter_score_trending_bonus": 0.5,
        "filter_score_volatile_bonus": 0.35,
        "filter_score_sideways_penalty": 0.25,
        "filter_confidence_bonus": 0.75,
        "filter_sideways_weight": 1.0,
        "filter_ema_weight": 0.9,
        "filter_volatility_weight": 0.9,
        "filter_vwap_weight": 0.8,
        "filter_volume_weight": 0.8,
        "filter_higher_tf_weight": 0.7,
        "max_filter_penalty_cap": 3.0,
    },
    "HIGH_PROFIT": {
        "capital_per_trade": 3000.0,
        "risk_per_trade_pct": 0.015,
        "max_capital_exposure": 75000.0,
        "max_premium": 350.0,
        "volume_spike_multiplier": 1.30,
        "max_daily_loss": 3000.0,
        "max_drawdown": 6500.0,
        "max_trades_per_day": 6,
        "cooldown_after_two_losses": 4,
        "partial_profit_rr": 1.20,
        "partial_profit_size_pct": 0.40,
        "time_exit_minutes": 40,
        "slippage_entry_factor": 1.012,
        "slippage_entry_factor_high": 1.025,
        "max_entry_slippage_pct": 0.035,
        "min_stop_loss_pct": 0.12,
        "max_stop_loss_pct": 0.38,
        "trailing_buffer_pct": 0.14,
        "filter_score_threshold": 2.9,
        "filter_score_trending_bonus": 0.6,
        "filter_score_volatile_bonus": 0.4,
        "filter_score_sideways_penalty": 0.2,
        "filter_confidence_bonus": 0.85,
        "filter_sideways_weight": 0.9,
        "filter_ema_weight": 0.8,
        "filter_volatility_weight": 0.8,
        "filter_vwap_weight": 0.7,
        "filter_volume_weight": 0.7,
        "filter_higher_tf_weight": 0.6,
        "max_filter_penalty_cap": 3.2,
    },
}


# NEW: NIFTY index options need softer scoring because valid intraday moves often start with
# only modest volume expansion and small VWAP/HTF deviations before accelerating.
NIFTY_OPTION_PROFILE_OVERRIDES: dict[str, dict[str, float | int | bool | str]] = {
    "STANDARD": {
        "min_premium": 60.0,
        "max_premium": 220.0,
        "volume_spike_multiplier": 1.20,
        "filter_score_threshold": 3.10,
        "filter_score_trending_bonus": 0.65,
        "filter_score_volatile_bonus": 0.45,
        "filter_score_sideways_penalty": 0.15,
        "filter_confidence_bonus": 0.90,
        "filter_sideways_weight": 0.80,
        "filter_ema_weight": 0.65,
        "filter_volatility_weight": 0.70,
        "filter_vwap_weight": 0.55,
        "filter_volume_weight": 0.55,
        "filter_higher_tf_weight": 0.45,
        "max_filter_penalty_cap": 3.25,
    },
    "HIGH_PROFIT": {
        "min_premium": 60.0,
        "max_premium": 260.0,
        "volume_spike_multiplier": 1.10,
        "filter_score_threshold": 3.30,
        "filter_score_trending_bonus": 0.80,
        "filter_score_volatile_bonus": 0.55,
        "filter_score_sideways_penalty": 0.10,
        "filter_confidence_bonus": 1.00,
        "filter_sideways_weight": 0.75,
        "filter_ema_weight": 0.55,
        "filter_volatility_weight": 0.60,
        "filter_vwap_weight": 0.45,
        "filter_volume_weight": 0.45,
        "filter_higher_tf_weight": 0.35,
        "max_filter_penalty_cap": 3.5,
    },
}


def _merged_profile_defaults(symbol: str, market_type: str, execution_profile: str) -> dict[str, float | int | bool | str]:
    defaults = dict(EXECUTION_PROFILE_DEFAULTS[execution_profile])
    normalized_symbol = _normalize_equity_symbol(symbol) if market_type == "EQUITY" else symbol.strip().upper()
    if market_type == "EQUITY" and normalized_symbol == "NIFTY":
        defaults.update(NIFTY_OPTION_PROFILE_OVERRIDES[execution_profile])
    return defaults


def get_settings() -> Settings:
    api_key = os.getenv("KITE_API_KEY", "").strip()
    access_token = os.getenv("KITE_ACCESS_TOKEN", "").strip()
    symbol = get_symbol()
    market_type = get_market_type()
    execution_profile = _get_execution_profile()
    profile_defaults = _merged_profile_defaults(symbol=symbol, market_type=market_type, execution_profile=execution_profile)

    if not api_key or not access_token:
        raise ValueError(
            "Missing Kite credentials. Set KITE_API_KEY and KITE_ACCESS_TOKEN in .env."
        )

    execution_settings = ExecutionSettings(
        capital_per_trade=_get_env_float("CAPITAL_PER_TRADE", float(profile_defaults["capital_per_trade"])),
        account_balance=_get_env_float("ACCOUNT_BALANCE", 200000.0),
        risk_per_trade_pct=_get_env_float("RISK_PER_TRADE_PCT", float(profile_defaults["risk_per_trade_pct"])),
        max_capital_exposure=_get_env_float("MAX_CAPITAL_EXPOSURE", float(profile_defaults["max_capital_exposure"])),
        stop_loss_percent=_get_env_float("STOP_LOSS_PERCENT", 0.20),
        trailing_stop_loss_percent=_get_env_float("TRAILING_STOP_LOSS_PERCENT", 0.20),
        max_retries=int(os.getenv("ORDER_MAX_RETRIES", "5")),
        retry_delay_seconds=_get_env_float("ORDER_RETRY_DELAY_SECONDS", 1.0),
        poll_attempts=int(os.getenv("ORDER_STATUS_POLL_ATTEMPTS", "10")),
        poll_interval_seconds=_get_env_float("ORDER_STATUS_POLL_INTERVAL_SECONDS", 1.0),
        default_product=os.getenv("ORDER_PRODUCT", "MIS").strip().upper() or "MIS",
        min_premium=_get_env_float("MIN_PREMIUM", 80.0),
        max_premium=_get_env_float("MAX_PREMIUM", float(profile_defaults["max_premium"])),
        adx_trending_threshold=_get_env_float("ADX_TRENDING_THRESHOLD", 25.0),
        adx_sideways_threshold=_get_env_float("ADX_SIDEWAYS_THRESHOLD", 20.0),
        atr_spike_multiplier=_get_env_float("ATR_SPIKE_MULTIPLIER", 1.5),
        range_compression_threshold_pct=_get_env_float("RANGE_COMPRESSION_THRESHOLD_PCT", 0.012),
        atr_stop_multiplier=_get_env_float("ATR_STOP_MULTIPLIER", 1.5),
        fixed_trail_step_pct=_get_env_float("FIXED_TRAIL_STEP_PCT", 0.08),
        volume_spike_multiplier=_get_env_float("VOLUME_SPIKE_MULTIPLIER", float(profile_defaults["volume_spike_multiplier"])),
        max_daily_loss=_get_env_float("MAX_DAILY_LOSS", float(profile_defaults["max_daily_loss"])),
        max_drawdown=_get_env_float("MAX_DRAWDOWN", float(profile_defaults["max_drawdown"])),
        max_trades_per_day=int(os.getenv("MAX_TRADES_PER_DAY", str(profile_defaults["max_trades_per_day"]))),
        cooldown_after_two_losses=int(os.getenv("COOLDOWN_AFTER_TWO_LOSSES", str(profile_defaults["cooldown_after_two_losses"]))),
        stop_after_three_losses=int(os.getenv("STOP_AFTER_THREE_LOSSES", "3")),
        partial_profit_rr=_get_env_float("PARTIAL_PROFIT_RR", float(profile_defaults["partial_profit_rr"])),
        partial_profit_size_pct=_get_env_float("PARTIAL_PROFIT_SIZE_PCT", float(profile_defaults["partial_profit_size_pct"])),
        time_exit_minutes=int(os.getenv("TIME_EXIT_MINUTES", str(profile_defaults["time_exit_minutes"]))),
        no_movement_threshold_pct=_get_env_float("NO_MOVEMENT_THRESHOLD_PCT", 0.05),
        hard_flatten_minutes_before_close=int(os.getenv("HARD_FLATTEN_MINUTES_BEFORE_CLOSE", "20")),
        slippage_entry_factor=_get_env_float("SLIPPAGE_ENTRY_FACTOR", float(profile_defaults["slippage_entry_factor"])),
        slippage_entry_factor_high=_get_env_float("SLIPPAGE_ENTRY_FACTOR_HIGH", float(profile_defaults["slippage_entry_factor_high"])),
        max_entry_slippage_pct=_get_env_float("MAX_ENTRY_SLIPPAGE_PCT", float(profile_defaults["max_entry_slippage_pct"])),
        weekly_expiry_cutoff_hour=int(os.getenv("WEEKLY_EXPIRY_CUTOFF_HOUR", "13")),
        kill_switch=_get_env_bool("KILL_SWITCH", False),
        trading_window_morning_start=os.getenv("TRADING_WINDOW_MORNING_START", "09:30").strip(),
        trading_window_morning_end=os.getenv("TRADING_WINDOW_MORNING_END", "11:30").strip(),
        trading_window_afternoon_start=os.getenv("TRADING_WINDOW_AFTERNOON_START", "13:30").strip(),
        trading_window_afternoon_end=os.getenv("TRADING_WINDOW_AFTERNOON_END", "14:30").strip(),
        stop_loss_mode=os.getenv("STOP_LOSS_MODE", "ATR").strip().upper() or "ATR",
        trailing_mode=os.getenv("TRAILING_MODE", "PREVIOUS_CANDLE").strip().upper() or "PREVIOUS_CANDLE",
        min_stop_loss_pct=_get_env_float("MIN_STOP_LOSS_PCT", float(profile_defaults["min_stop_loss_pct"])),
        max_stop_loss_pct=_get_env_float("MAX_STOP_LOSS_PCT", float(profile_defaults["max_stop_loss_pct"])),
        trailing_rr_lock_step=_get_env_float("TRAILING_RR_LOCK_STEP", 0.75),
        trailing_buffer_pct=_get_env_float("TRAILING_BUFFER_PCT", float(profile_defaults["trailing_buffer_pct"])),
        drawdown_size_reduction_start_pct=_get_env_float("DRAWDOWN_SIZE_REDUCTION_START_PCT", 0.5),
        drawdown_size_reduction_factor=_get_env_float("DRAWDOWN_SIZE_REDUCTION_FACTOR", 0.5),
        enable_vwap_filter=_get_env_bool("ENABLE_VWAP_FILTER", True),
        enable_volume_filter=_get_env_bool("ENABLE_VOLUME_FILTER", True),
        enable_volatility_filter=_get_env_bool("ENABLE_VOLATILITY_FILTER", True),
        enable_higher_timeframe_trend_filter=_get_env_bool("ENABLE_HIGHER_TIMEFRAME_TREND_FILTER", True),
        higher_timeframe_multiple=int(os.getenv("HIGHER_TIMEFRAME_MULTIPLE", "5")),
        higher_timeframe_fast_ema=int(os.getenv("HIGHER_TIMEFRAME_FAST_EMA", "9")),
        higher_timeframe_slow_ema=int(os.getenv("HIGHER_TIMEFRAME_SLOW_EMA", "21")),
        filter_score_threshold=_get_env_float("FILTER_SCORE_THRESHOLD", float(profile_defaults["filter_score_threshold"])),
        filter_score_trending_bonus=_get_env_float("FILTER_SCORE_TRENDING_BONUS", float(profile_defaults["filter_score_trending_bonus"])),
        filter_score_volatile_bonus=_get_env_float("FILTER_SCORE_VOLATILE_BONUS", float(profile_defaults["filter_score_volatile_bonus"])),
        filter_score_sideways_penalty=_get_env_float("FILTER_SCORE_SIDEWAYS_PENALTY", float(profile_defaults["filter_score_sideways_penalty"])),
        filter_confidence_bonus=_get_env_float("FILTER_CONFIDENCE_BONUS", float(profile_defaults["filter_confidence_bonus"])),
        filter_sideways_weight=_get_env_float("FILTER_SIDEWAYS_WEIGHT", float(profile_defaults["filter_sideways_weight"])),
        filter_ema_weight=_get_env_float("FILTER_EMA_WEIGHT", float(profile_defaults["filter_ema_weight"])),
        filter_volatility_weight=_get_env_float("FILTER_VOLATILITY_WEIGHT", float(profile_defaults["filter_volatility_weight"])),
        filter_vwap_weight=_get_env_float("FILTER_VWAP_WEIGHT", float(profile_defaults["filter_vwap_weight"])),
        filter_volume_weight=_get_env_float("FILTER_VOLUME_WEIGHT", float(profile_defaults["filter_volume_weight"])),
        filter_higher_tf_weight=_get_env_float("FILTER_HIGHER_TF_WEIGHT", float(profile_defaults["filter_higher_tf_weight"])),
        max_filter_penalty_cap=_get_env_float("MAX_FILTER_PENALTY_CAP", float(profile_defaults["max_filter_penalty_cap"])),
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
        execution_profile=execution_profile,
        candle_interval_minutes=int(os.getenv("CANDLE_INTERVAL_MINUTES", "3")),
        stale_tick_warning_seconds=int(os.getenv("STALE_TICK_WARNING_SECONDS", "45")),
        stale_candle_warning_seconds=int(os.getenv("STALE_CANDLE_WARNING_SECONDS", "300")),
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


def _get_execution_profile() -> str:
    raw_value = os.getenv("EXECUTION_PROFILE", "STANDARD").strip().upper()
    if raw_value not in EXECUTION_PROFILE_DEFAULTS:
        raise ValueError(
            f"Unsupported EXECUTION_PROFILE={raw_value}. Supported profiles: {sorted(EXECUTION_PROFILE_DEFAULTS)}"
        )
    return raw_value


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

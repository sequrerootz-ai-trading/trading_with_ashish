from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime

from config import get_mode
from data.candle_store import Candle
from data.database import TradingDatabase
from strategy.market_regime import detect_market_regime
from strategy.nifty_options import generate_nifty_options_signal
from strategy.signal_types import GeneratedSignal, SignalContext
from strategy.strategy_equity import generate_equity_signal

logger = logging.getLogger(__name__)

_last_signal_time: dict[str, datetime] = defaultdict(lambda: datetime.min.replace(tzinfo=UTC))
_daily_trade_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
_last_signal_direction: dict[str, str] = {}


def _is_valid_signal(signal: GeneratedSignal) -> bool:
    return signal is not None and signal.signal not in {"NO_TRADE", "", None}


def _get_confidence_threshold(data: SignalContext) -> float:
    if data.timeframe_minutes <= 3:
        return 0.45
    if data.timeframe_minutes <= 5:
        return 0.50
    return 0.55


def _passes_confidence_filter(signal: GeneratedSignal, data: SignalContext) -> tuple[bool, str]:
    threshold = _get_confidence_threshold(data)
    if signal.confidence < threshold - 0.07:
        return False, f"low_confidence_strict<{threshold}"
    if signal.confidence < threshold:
        return True, "weak_confidence_allowed"
    return True, "strong_confidence"


def _check_market_regime(data: SignalContext) -> tuple[bool, str]:
    if len(data.candles) < 20:
        return True, "regime_warmup"
    try:
        regime = detect_market_regime(data.candles)
        if regime.regime == "SIDEWAYS":
            vol_spike = regime.volume_spike_ratio or 0.0
            adx = regime.adx or 0.0
            if adx > 18:
                return True, "early_trend_allowed"
            if vol_spike >= 1.2:
                return True, "sideways_volume_breakout"
            return False, "sideways_blocked"
        return True, regime.regime.lower()
    except Exception as exc:
        logger.warning("[REGIME] Detection error - allowing trade: %s", exc)
        return True, "regime_error"


def _dynamic_cooldown(data: SignalContext) -> float:
    if data.timeframe_minutes <= 3:
        return 2.0
    if data.timeframe_minutes <= 5:
        return 3.0
    return 5.0


def _check_trade_cooldown(symbol: str, cooldown_minutes: float) -> tuple[bool, str]:
    now = datetime.now(UTC)
    last = _last_signal_time[symbol]
    elapsed = (now - last).total_seconds() / 60.0
    if elapsed < cooldown_minutes:
        remaining = cooldown_minutes - elapsed
        return False, f"cooldown_active_{remaining:.1f}min"
    return True, "cooldown_ok"


def _check_daily_trade_limit(symbol: str, max_trades: int = 10) -> tuple[bool, str]:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    count = _daily_trade_counts[symbol][today]
    if count >= max_trades:
        return False, f"daily_limit_reached_{count}"
    return True, f"trades_today_{count}"


def _record_signal_fired(symbol: str, signal_type: str) -> None:
    now = datetime.now(UTC)
    today = now.strftime("%Y-%m-%d")
    _last_signal_time[symbol] = now
    _daily_trade_counts[symbol][today] += 1
    _last_signal_direction[symbol] = signal_type


def generate_equity_signal_engine(
    symbol: str,
    data: SignalContext,
    sentiment: dict[str, object] | None = None,
    max_trades_per_day: int = 10,
) -> GeneratedSignal:
    normalized_symbol = symbol.strip().upper()
    now_ts = datetime.now(UTC).isoformat()

    regime_ok, regime_reason = _check_market_regime(data)
    if not regime_ok:
        return GeneratedSignal(symbol, now_ts, "NO_TRADE", regime_reason, 0.0)

    limit_ok, limit_reason = _check_daily_trade_limit(symbol, max_trades_per_day)
    if not limit_ok:
        return GeneratedSignal(symbol, now_ts, "NO_TRADE", limit_reason, 0.0)

    try:
        if normalized_symbol == "NIFTY":
            signal = generate_nifty_options_signal(data)
        else:
            signal = generate_equity_signal(symbol, data, sentiment or _default_sentiment())
    except Exception as exc:
        logger.error("[EQUITY_STRATEGY_ERROR] %s - %s", symbol, exc)
        return GeneratedSignal(symbol, now_ts, "NO_TRADE", "strategy_exception", 0.0)

    if not _is_valid_signal(signal):
        return GeneratedSignal(symbol, now_ts, "NO_TRADE", "invalid_signal", 0.0)

    passes, confidence_reason = _passes_confidence_filter(signal, data)
    if not passes:
        return GeneratedSignal(symbol, now_ts, "NO_TRADE", confidence_reason, signal.confidence)

    cooldown_minutes = _dynamic_cooldown(data)
    if _last_signal_direction.get(symbol) == signal.signal:
        cooldown_minutes *= 0.5
    cooldown_ok, cooldown_reason = _check_trade_cooldown(symbol, cooldown_minutes)
    if get_mode().upper() != "PAPER" and not cooldown_ok:
        return GeneratedSignal(symbol, now_ts, "NO_TRADE", cooldown_reason, 0.0)

    _record_signal_fired(symbol, signal.signal)
    logger.info("[EQUITY_SIGNAL] %s | %s | conf=%.2f | %s | %s", symbol, signal.signal, signal.confidence, regime_reason, confidence_reason)
    return signal


def get_last_closed_candle(symbol: str, database: TradingDatabase) -> Candle | None:
    return database.get_last_closed_candle(symbol)


def store_market_data(data: Candle, database: TradingDatabase) -> bool:
    return database.store_market_data(data)


def store_signal(signal: GeneratedSignal, database: TradingDatabase) -> None:
    database.store_signal(signal.symbol, signal.timestamp or "", signal.signal, signal.reason)


def _default_sentiment() -> dict[str, object]:
    return {}

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime

from config import get_market_type, get_mode
from data.candle_store import Candle
from data.database import TradingDatabase
from strategy.market_regime import detect_market_regime
from strategy.mcx_option_helper import enrich_mcx_signal_with_option
from strategy.nifty_options import generate_nifty_options_signal
from strategy.signal_types import GeneratedSignal, SignalContext
from strategy.strategy_equity import generate_equity_signal
from strategy.strategy_mcx import generate_mcx_signal

logger = logging.getLogger(__name__)

_last_signal_time: dict[str, datetime] = defaultdict(lambda: datetime.min.replace(tzinfo=UTC))
_daily_trade_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))


def _is_valid_signal(signal: GeneratedSignal) -> bool:
    return signal is not None and signal.signal != "NO_TRADE"


def _get_confidence_threshold(data: SignalContext) -> float:
    if data.timeframe_minutes <= 3:
        return 0.45   # was 0.50
    if data.timeframe_minutes <= 5:
        return 0.50   # was 0.55
    return 0.55       # was 0.60


def _passes_confidence_filter(signal: GeneratedSignal, data: SignalContext) -> bool:
    threshold = _get_confidence_threshold(data)
    return signal.confidence >= threshold


def _check_market_regime(data: SignalContext) -> tuple[bool, str]:
    if len(data.candles) < 20:
        return True, "regime_warmup"

    try:
        regime = detect_market_regime(data.candles)
        if regime.regime == "SIDEWAYS":
            vol_spike = regime.volume_spike_ratio or 0.0
            if vol_spike >= 1.5:
                logger.info(
                    "[REGIME] SIDEWAYS but volume spike=%.2f - allowing (breakout watch)",
                    vol_spike,
                )
                return True, "sideways_with_volume_spike"

            logger.info(
                "[REGIME] Blocked - SIDEWAYS (ADX=%.1f, range=%.3f, vol_ratio=%.2f)",
                regime.adx or 0.0,
                regime.range_pct,
                vol_spike,
            )
            return False, "sideways_market_blocked"

        logger.debug("[REGIME] Allowed - regime=%s ADX=%.1f", regime.regime, regime.adx or 0.0)
        return True, regime.regime.lower()
    except Exception as exc:
        logger.warning("[REGIME] Detection error - allowing trade: %s", exc)
        return True, "regime_check_error"


def _check_trade_cooldown(symbol: str, cooldown_minutes: int = 5) -> tuple[bool, str]:
    now = datetime.now(UTC)
    last = _last_signal_time[symbol]
    elapsed = (now - last).total_seconds() / 60.0
    if elapsed < cooldown_minutes:
        remaining = cooldown_minutes - elapsed
        logger.info("[COOLDOWN] %s - %.1f min remaining", symbol, remaining)
        return False, f"cooldown_active_{remaining:.1f}min"
    return True, "cooldown_ok"


# ✅ UPDATED: Removed daily trade limit restriction
def _check_daily_trade_limit(symbol: str, max_trades: int = 5) -> tuple[bool, str]:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    count = _daily_trade_counts[symbol][today]

    # ❌ OLD LOGIC (REMOVED)
    # if count >= max_trades:
    #     logger.info("[DAILY_LIMIT] %s - max %d trades reached (count=%d)", symbol, max_trades, count)
    #     return False, f"daily_limit_reached_{count}"

    # ✅ NEW: Always allow trades
    return True, f"trades_today_{count}"


def _record_signal_fired(symbol: str) -> None:
    now = datetime.now(UTC)
    today = now.strftime("%Y-%m-%d")
    _last_signal_time[symbol] = now
    _daily_trade_counts[symbol][today] += 1
    logger.debug(
        "[TRADE_CONTROL] %s - recorded | today_count=%d",
        symbol,
        _daily_trade_counts[symbol][today],
    )


def get_last_closed_candle(symbol: str, database: TradingDatabase) -> Candle | None:
    return database.get_last_closed_candle(symbol)


def store_market_data(data: Candle, database: TradingDatabase) -> bool:
    return database.store_market_data(data)


def generate_signal(
    symbol: str,
    market_type: str,
    data: SignalContext,
    sentiment: dict[str, object] | None = None,
    cooldown_minutes: int = 5,
    max_trades_per_day: int = 5,
) -> GeneratedSignal:
    normalized_market_type = (market_type or get_market_type()).strip().upper()
    now_ts = datetime.now(UTC).isoformat()

    regime_ok, regime_reason = _check_market_regime(data)
    if not regime_ok:
        return GeneratedSignal(
            symbol=symbol,
            timestamp=now_ts,
            signal="NO_TRADE",
            reason=regime_reason,
            confidence=0.0,
        )

    limit_ok, limit_reason = _check_daily_trade_limit(symbol, max_trades_per_day)
    if not limit_ok:
        return GeneratedSignal(
            symbol=symbol,
            timestamp=now_ts,
            signal="NO_TRADE",
            reason=limit_reason,
            confidence=0.0,
        )

    cooldown_ok, cooldown_reason = _check_trade_cooldown(symbol, cooldown_minutes)
    if get_mode().upper() != "PAPER" and not cooldown_ok:
        return GeneratedSignal(
            symbol=symbol,
            timestamp=now_ts,
            signal="NO_TRADE",
            reason=cooldown_reason,
            confidence=0.0,
        )

    normalized_symbol = symbol.strip().upper()
    if normalized_market_type == "MCX":
        signal = generate_mcx_signal(symbol, data)
        signal = enrich_mcx_signal_with_option(
            symbol=symbol,
            generated_signal=signal,
            spot_price=float(data.last_candle.close) if data.last_candle is not None else 0.0,
            option_chain=(sentiment or {}).get("option_chain"),
        )
    elif normalized_market_type == "EQUITY" and normalized_symbol == "NIFTY":
        signal = generate_nifty_options_signal(data)
    else:
        signal = generate_equity_signal(symbol, data, sentiment or _default_sentiment())

    if signal is None:
        logger.warning("[SIGNAL_ENGINE] %s - strategy returned None", symbol)
        return GeneratedSignal(
            symbol=symbol,
            timestamp=now_ts,
            signal="NO_TRADE",
            reason="strategy_returned_none",
            confidence=0.0,
        )

    if _is_valid_signal(signal) and not _passes_confidence_filter(signal, data):
        threshold = _get_confidence_threshold(data)
        logger.info(
            "[FILTER] %s low confidence %.2f < %.2f",
            symbol, signal.confidence, threshold,
        )
        return GeneratedSignal(
            symbol=symbol,
            timestamp=signal.timestamp,
            signal="NO_TRADE",
            reason=f"low_confidence_filtered<{threshold}",
            confidence=signal.confidence,
        )

    if _is_valid_signal(signal):
        _record_signal_fired(symbol)

    option_label, expiry_label = _signal_option_context(signal)
    logger.info(
        "[SIGNAL_ENGINE] %s | Signal=%s | Confidence=%.2f | Regime=%s%s%s",
        symbol,
        signal.signal,
        signal.confidence,
        regime_reason,
        f" | Option={option_label}" if option_label else "",
        f" | Expiry={expiry_label}" if expiry_label else "",
    )
    _log_option_decision(symbol, signal)
    return signal


def store_signal(signal: GeneratedSignal, database: TradingDatabase) -> None:
    if signal.timestamp is None:
        logger.warning("[SIGNAL_ENGINE] %s has no timestamp - storing empty string", signal.symbol)
    success = database.store_signal(
        signal.symbol,
        signal.timestamp or "",
        signal.signal,
        signal.reason,
    )
    if not success:
        logger.warning(
            "[SIGNAL_ENGINE] Failed to store signal for %s | signal=%s",
            signal.symbol, signal.signal,
        )


def _default_sentiment() -> dict[str, object]:
    return {
        "sentiment": "SIDEWAYS",
        "confidence": 0.0,
        "reason": "sentiment_disabled",
    }

def _signal_option_context(signal: GeneratedSignal) -> tuple[str | None, str | None]:
    details = getattr(signal, "details", None)
    option = getattr(details, "option_suggestion", None)
    if option is None:
        return None, None

    option_label = None
    strike = getattr(option, "strike", None)
    option_type = getattr(option, "option_type", None)
    trading_symbol = getattr(option, "trading_symbol", None)
    label = getattr(option, "label", None)
    if strike is not None and option_type:
        option_label = f"{strike} {option_type}"
    elif trading_symbol:
        option_label = str(trading_symbol)
    elif label:
        option_label = str(label)

    expiry = getattr(option, "expiry", None)
    return option_label, (str(expiry) if expiry else None)


def _log_option_decision(symbol: str, signal: GeneratedSignal) -> None:
    option_label, expiry_label = _signal_option_context(signal)
    if option_label is None:
        return

    reason = signal.reason or "unspecified"
    verdict = "Accepted" if signal.signal not in {"NO_TRADE", "", None} else "Rejected"
    logger.info(
        "[OPTION_DECISION] %s %s%s | %s | Reason=%s",
        symbol,
        option_label,
        f" | Expiry={expiry_label}" if expiry_label else "",
        verdict,
        reason,
    )


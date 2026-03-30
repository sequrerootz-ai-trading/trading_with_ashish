from __future__ import annotations

import logging
from datetime import datetime

from data.database import TradingDatabase
from strategy.indicators import calculate_indicators
from strategy.signal_types import GeneratedSignal, SignalContext


logger = logging.getLogger(__name__)
TOP_STOCKS = [
    "HDFCBANK", "RELIANCE", "ICICIBANK", "INFY", "TCS",
    "KOTAKBANK", "LT", "AXISBANK", "SBIN", "ITC",
]
MIN_CONFIRMING_STOCKS = 6
MIN_INDEX_CANDLES = 21
_LOOKBACK_CANDLES = 30
_DB: TradingDatabase | None = None
_STOCK_CANDLE_CACHE: dict[tuple[str, str], list] = {}


def generate_nifty_hybrid_signal(data: SignalContext) -> GeneratedSignal:
    last_candle = data.last_candle
    timestamp = last_candle.end.isoformat() if last_candle is not None else datetime.now().isoformat()
    if last_candle is None or len(data.candles) < MIN_INDEX_CANDLES:
        return GeneratedSignal(
            symbol=data.symbol,
            timestamp=timestamp,
            signal="NO_TRADE",
            reason="nifty_hybrid_insufficient_index_data",
            confidence=0.0,
            context={"model": "NIFTY_HYBRID"},
        )

    index_signal = _build_index_signal(data)
    logger.info(
        "[NIFTY_INDEX] Bias=%s | Trend=%s | EMA9=%.2f | EMA21=%.2f | Breakout=%.2f | Breakdown=%.2f | RSI=%s",
        index_signal["bias"],
        index_signal["trend"],
        index_signal["ema_9"],
        index_signal["ema_21"],
        index_signal["breakout"],
        index_signal["breakdown"],
        _fmt_optional(index_signal["rsi"]),
    )

    stock_biases: list[dict[str, str]] = []
    for stock_symbol in TOP_STOCKS:
        candles = _get_stock_candles(stock_symbol, timestamp)
        if len(candles) < MIN_INDEX_CANDLES:
            continue
        stock_biases.append({
            "symbol": stock_symbol,
            "bias": _analyze_stock_bias(candles),
        })

    available_count = len(stock_biases)
    bullish_count = sum(1 for item in stock_biases if item["bias"] == "BULLISH")
    bearish_count = sum(1 for item in stock_biases if item["bias"] == "BEARISH")
    neutral_count = sum(1 for item in stock_biases if item["bias"] == "NEUTRAL")

    if available_count < MIN_CONFIRMING_STOCKS:
        logger.info(
            "[NIFTY_BREADTH] Bullish=%s | Bearish=%s | Neutral=%s | Score=NA | Available=%s",
            bullish_count,
            bearish_count,
            neutral_count,
            available_count,
        )
        logger.info(
            "[NIFTY_HYBRID] Signal=NO_TRADE | Confidence=0.00 | Reason=Insufficient stock breadth data (%s/%s)",
            available_count,
            MIN_CONFIRMING_STOCKS,
        )
        return GeneratedSignal(
            symbol=data.symbol,
            timestamp=timestamp,
            signal="NO_TRADE",
            reason="insufficient_breadth_data",
            confidence=0.0,
            context={
                "model": "NIFTY_HYBRID",
                "index_bias": index_signal["bias"],
                "index_trend": index_signal["trend"],
                "index_strength": index_signal["strength"],
                "breadth_score": None,
                "breadth_strength": "INSUFFICIENT_DATA",
                "bullish_count": bullish_count,
                "bearish_count": bearish_count,
                "neutral_count": neutral_count,
                "available_stocks": available_count,
            },
        )

    breadth_score = round(bullish_count / available_count, 2)
    bearish_score = round(bearish_count / available_count, 2)
    if breadth_score >= 0.7:
        breadth_strength = "STRONG_BULLISH"
    elif breadth_score <= 0.3:
        breadth_strength = "STRONG_BEARISH"
    else:
        breadth_strength = "WEAK_OR_MIXED"

    logger.info(
        "[NIFTY_BREADTH] Bullish=%s | Bearish=%s | Neutral=%s | Score=%.2f",
        bullish_count,
        bearish_count,
        neutral_count,
        breadth_score,
    )

    final_signal = "NO_TRADE"
    reason = "Index and breadth not aligned"
    breadth_alignment = max(breadth_score, bearish_score)

    if index_signal["bias"] == "SIDEWAYS":
        reason = "NIFTY index is sideways"
    elif index_signal["bias"] == "BULLISH" and breadth_strength == "STRONG_BULLISH":
        final_signal = "BUY"
        reason = "Index+Breadth aligned"
        breadth_alignment = breadth_score
    elif index_signal["bias"] == "BEARISH" and breadth_strength == "STRONG_BEARISH":
        final_signal = "SELL"
        reason = "Index+Breadth aligned"
        breadth_alignment = bearish_score
    elif index_signal["bias"] == "BULLISH":
        reason = "Bullish index but breadth confirmation missing"
    elif index_signal["bias"] == "BEARISH":
        reason = "Bearish index but breadth confirmation missing"

    confidence = round(min(1.0, (index_signal["strength"] * 0.6) + (breadth_alignment * 0.4)), 2)
    logger.info(
        "[NIFTY_HYBRID] Signal=%s | Confidence=%.2f | Reason=%s",
        final_signal,
        confidence,
        reason,
    )

    return GeneratedSignal(
        symbol=data.symbol,
        timestamp=timestamp,
        signal=final_signal,
        reason=reason,
        confidence=confidence,
        context={
            "model": "NIFTY_HYBRID",
            "index_bias": index_signal["bias"],
            "index_trend": index_signal["trend"],
            "index_strength": index_signal["strength"],
            "breadth_score": breadth_score,
            "bearish_breadth_score": bearish_score,
            "breadth_strength": breadth_strength,
            "bullish_count": bullish_count,
            "bearish_count": bearish_count,
            "neutral_count": neutral_count,
            "available_stocks": available_count,
            "confirmed_symbols": [item["symbol"] for item in stock_biases],
        },
    )


def _build_index_signal(data: SignalContext) -> dict[str, float | str | None]:
    candles = data.candles
    close_prices = [float(candle.close) for candle in candles]
    indicators = calculate_indicators(close_prices)
    current_candle = candles[-1]
    recent_window = candles[-6:-1] if len(candles) >= 6 else candles[:-1]
    breakout = max(float(candle.high) for candle in recent_window) if recent_window else float(current_candle.high)
    breakdown = min(float(candle.low) for candle in recent_window) if recent_window else float(current_candle.low)
    ema_9 = float(indicators.ema_9 or 0.0)
    ema_21 = float(indicators.ema_21 or 0.0)
    close_price = float(current_candle.close)
    rsi = indicators.rsi

    bias = "SIDEWAYS"
    if indicators.ema_9 is not None and indicators.ema_21 is not None:
        if ema_9 > ema_21 and close_price > breakout:
            bias = "BULLISH"
        elif ema_9 < ema_21 and close_price < breakdown:
            bias = "BEARISH"

    ema_gap_pct = abs(ema_9 - ema_21) / max(close_price, 0.01)
    breakout_pct = 0.0
    if bias == "BULLISH":
        breakout_pct = max(close_price - breakout, 0.0) / max(close_price, 0.01)
    elif bias == "BEARISH":
        breakout_pct = max(breakdown - close_price, 0.0) / max(close_price, 0.01)
    strength = round(min(1.0, 0.35 + (ema_gap_pct * 80) + (breakout_pct * 120)), 2) if bias != "SIDEWAYS" else 0.0
    trend = "TRENDING" if bias != "SIDEWAYS" and ema_gap_pct >= 0.0015 else "RANGING"
    reason = "ema_crossover_plus_breakout" if bias == "BULLISH" else "ema_crossdown_plus_breakdown" if bias == "BEARISH" else "index_sideways"

    return {
        "bias": bias,
        "trend": trend,
        "strength": strength,
        "reason": reason,
        "ema_9": round(ema_9, 2),
        "ema_21": round(ema_21, 2),
        "breakout": round(breakout, 2),
        "breakdown": round(breakdown, 2),
        "rsi": None if rsi is None else round(float(rsi), 2),
    }


def _analyze_stock_bias(candles: list) -> str:
    close_prices = [float(candle.close) for candle in candles]
    indicators = calculate_indicators(close_prices)
    if indicators.ema_9 is None or indicators.ema_21 is None:
        return "NEUTRAL"
    last_close = float(candles[-1].close)
    ema_9 = float(indicators.ema_9)
    ema_21 = float(indicators.ema_21)
    if last_close >= ema_9 and ema_9 > ema_21:
        return "BULLISH"
    if last_close <= ema_9 and ema_9 < ema_21:
        return "BEARISH"
    return "NEUTRAL"


def _get_stock_candles(symbol: str, reference_timestamp: str) -> list:
    cache_key = (symbol, reference_timestamp)
    cached = _STOCK_CANDLE_CACHE.get(cache_key)
    if cached is not None:
        return cached
    candles = _database().get_recent_candles(symbol, limit=_LOOKBACK_CANDLES)
    _STOCK_CANDLE_CACHE[cache_key] = candles
    return candles


def _database() -> TradingDatabase:
    global _DB
    if _DB is None:
        _DB = TradingDatabase()
    return _DB


def _fmt_optional(value: float | None) -> str:
    if value is None:
        return "NA"
    return f"{value:.2f}"

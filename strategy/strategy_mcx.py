from __future__ import annotations

from strategy.indicators import calculate_indicators, detect_trend
from strategy.signal_types import GeneratedSignal, SignalContext


def generate_mcx_signal(symbol: str, data: SignalContext) -> GeneratedSignal:
    if data.last_candle is None or len(data.candles) < 21:
        return GeneratedSignal(
            symbol=symbol,
            timestamp="",
            signal="NO_TRADE",
            reason="insufficient_closed_candles",
            confidence=0.0,
        )

    close_prices = [candle.close for candle in data.candles]
    indicators = calculate_indicators(close_prices)
    current_candle = data.last_candle
    previous_candle = data.candles[-2]
    trend = detect_trend(indicators.ema_9, indicators.ema_21)
    fast_timeframe = data.timeframe_minutes <= 3

    recent_window = data.candles[-4:-1] if fast_timeframe else data.candles[-6:-1]
    breakout_level = max(candle.high for candle in recent_window)
    breakdown_level = min(candle.low for candle in recent_window)
    candle_range = max(current_candle.high - current_candle.low, 0.0)
    close_position = 0.5 if candle_range == 0 else (current_candle.close - current_candle.low) / candle_range
    bullish_close_threshold = 0.55 if fast_timeframe else 0.65
    bearish_close_threshold = 0.45 if fast_timeframe else 0.35

    bullish_break = (
        current_candle.close >= breakout_level if fast_timeframe else current_candle.close > breakout_level
    ) or (
        fast_timeframe
        and current_candle.high >= breakout_level
        and close_position >= bullish_close_threshold
    )
    bearish_break = (
        current_candle.close <= breakdown_level if fast_timeframe else current_candle.close < breakdown_level
    ) or (
        fast_timeframe
        and current_candle.low <= breakdown_level
        and close_position <= bearish_close_threshold
    )

    signal = "NO_TRADE"
    confidence = 0.0
    reason: list[str] = []

    if (
        trend == "bullish"
        and bullish_break
        and current_candle.high >= previous_candle.high
        and close_position >= bullish_close_threshold
    ):
        signal = "BUY"
        confidence = 0.70 if fast_timeframe else 0.78
        reason.extend(["ema_trend_up", "commodity_breakout"])
        if fast_timeframe and current_candle.close < breakout_level:
            reason.append("wick_breakout_confirmation")
    elif (
        trend == "bearish"
        and bearish_break
        and current_candle.low <= previous_candle.low
        and close_position <= bearish_close_threshold
    ):
        signal = "SELL"
        confidence = 0.70 if fast_timeframe else 0.78
        reason.extend(["ema_trend_down", "commodity_breakdown"])
        if fast_timeframe and current_candle.close > breakdown_level:
            reason.append("wick_breakdown_confirmation")
    else:
        reason.append("commodity_filter_not_met")

    reason.extend(
        [
            f"ema9={_fmt(indicators.ema_9)}",
            f"ema21={_fmt(indicators.ema_21)}",
            f"trend={trend}",
            f"timeframe={data.timeframe_minutes}m",
            f"breakout={breakout_level:.2f}",
            f"breakdown={breakdown_level:.2f}",
        ]
    )

    return GeneratedSignal(
        symbol=symbol,
        timestamp=current_candle.end.isoformat(),
        signal=signal,
        reason=" ".join(reason),
        confidence=confidence,
    )


def _fmt(value: float | None) -> str:
    if value is None:
        return "NA"
    return f"{value:.2f}"

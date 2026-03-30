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
    prev_reference = data.candles[-3]

    trend = detect_trend(indicators.ema_9, indicators.ema_21)
    fast_timeframe = data.timeframe_minutes <= 3

    # =========================
    # 🔹 Breakout Levels
    # =========================
    recent_window = data.candles[-4:-1] if fast_timeframe else data.candles[-6:-1]
    breakout_level = max(c.high for c in recent_window)
    breakdown_level = min(c.low for c in recent_window)

    # =========================
    # 🔹 Candle Strength
    # =========================
    candle_range = max(current_candle.high - current_candle.low, 0.0)
    close_position = (
        0.5 if candle_range == 0 else (current_candle.close - current_candle.low) / candle_range
    )

    bullish_close_threshold = 0.50 if fast_timeframe else 0.58
    bearish_close_threshold = 0.50 if fast_timeframe else 0.42

    # =========================
    # 🔹 Breakout Detection
    # =========================
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

    # =========================
    # 🔹 BOOSTERS (Scoring)
    # =========================

    # Volatility
    recent_ranges = [(c.high - c.low) for c in data.candles[-6:-1]]
    avg_range = (sum(recent_ranges) / len(recent_ranges)) if recent_ranges else 0.0
    current_range = current_candle.high - current_candle.low
    volatility_ok = current_range >= avg_range * 0.9 if avg_range > 0 else True

    # Momentum
    momentum = abs(current_candle.close - previous_candle.close)
    prev_momentum = abs(previous_candle.close - prev_reference.close)
    momentum_ok = momentum >= prev_momentum * 0.8

    # Strong close
    strong_close_buy = close_position > 0.55
    strong_close_sell = close_position < 0.45

    # =========================
    # 🔹 SIGNAL LOGIC (HYBRID)
    # =========================
    signal = "NO_TRADE"
    confidence = 0.0
    reason: list[str] = []

    # =========================
    # ✅ BUY
    # =========================
    if (
        trend == "bullish"
        and bullish_break
        and close_position >= bullish_close_threshold
    ):
        score = 0

        if volatility_ok:
            score += 1
        if momentum_ok:
            score += 1
        if strong_close_buy:
            score += 1

        if score >= 1:  # 🔥 KEY: allows high frequency
            signal = "BUY"

            confidence = 0.6 + (score * 0.1)
            confidence = min(confidence, 0.85)

            reason.extend(["ema_trend_up", "commodity_breakout", f"score={score}"])

            if fast_timeframe and current_candle.close < breakout_level:
                reason.append("wick_breakout")

    # =========================
    # ✅ SELL
    # =========================
    elif (
        trend == "bearish"
        and bearish_break
        and close_position <= bearish_close_threshold
    ):
        score = 0

        if volatility_ok:
            score += 1
        if momentum_ok:
            score += 1
        if strong_close_sell:
            score += 1

        if score >= 1:
            signal = "SELL"

            confidence = 0.6 + (score * 0.1)
            confidence = min(confidence, 0.85)

            reason.extend(["ema_trend_down", "commodity_breakdown", f"score={score}"])

            if fast_timeframe and current_candle.close > breakdown_level:
                reason.append("wick_breakdown")

    else:
        reason.append("commodity_filter_not_met")

    # =========================
    # 🔹 Debug Logs
    # =========================
    reason.extend(
        [
            f"ema9={_fmt(indicators.ema_9)}",
            f"ema21={_fmt(indicators.ema_21)}",
            f"trend={trend}",
            f"timeframe={data.timeframe_minutes}m",
            f"breakout={breakout_level:.2f}",
            f"breakdown={breakdown_level:.2f}",
            f"volatility_ok={volatility_ok}",
            f"momentum_ok={momentum_ok}",
            f"close_pos={close_position:.2f}",
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
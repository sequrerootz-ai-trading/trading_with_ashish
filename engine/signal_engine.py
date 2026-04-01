from __future__ import annotations

from strategy.indicators import calculate_indicators, detect_trend
from strategy.signal_types import SignalContext
from utils.calculations import compute_close_position, compute_volume_ratio


LOOKBACK_CANDLES = 5
MIN_REQUIRED_CANDLES = 8
FAST_TIMEFRAME_MINUTES = 3
BULLISH_BREAKOUT_BUFFER = 0.999
BEARISH_BREAKDOWN_BUFFER = 1.001
MOMENTUM_FACTOR = 0.8
MIN_VOLUME_RATIO = 0.9
MIN_VOLATILITY_FACTOR = 0.7
FAST_BUY_CLOSE_POSITION = 0.55
FAST_SELL_CLOSE_POSITION = 0.45
SLOW_BUY_CLOSE_POSITION = 0.55
SLOW_SELL_CLOSE_POSITION = 0.45
MIN_SCORE_TO_TRIGGER = 2
SIDEWAYS_TREND_STRENGTH_PCT = 0.0003
TARGET_RISK_MULTIPLIER = 1.2
STOP_RISK_MULTIPLIER = 0.8


def evaluate_nifty_price_action(data: SignalContext) -> dict[str, object]:
    if data.last_candle is None or len(data.candles) < MIN_REQUIRED_CANDLES:
        return _empty_result(reason="insufficient_candles")

    close_prices = [float(candle.close) for candle in data.candles]
    indicators = calculate_indicators(close_prices)
    current_candle = data.last_candle
    previous_candle = data.candles[-2]
    prev_reference = data.candles[-3]
    trend = detect_trend(indicators.ema_9, indicators.ema_21)
    current_close = float(current_candle.close)
    current_high = float(current_candle.high)
    current_low = float(current_candle.low)
    price = max(current_close, 0.01)

    recent_window = data.candles[-LOOKBACK_CANDLES:]
    breakout_level = max(float(candle.high) for candle in recent_window)
    breakdown_level = min(float(candle.low) for candle in recent_window)
    close_position = compute_close_position(current_high, current_low, current_close)

    bullish_break = current_close > breakout_level * BULLISH_BREAKOUT_BUFFER or current_high > breakout_level
    bearish_break = current_close < breakdown_level * BEARISH_BREAKDOWN_BUFFER or current_low < breakdown_level

    recent_ranges = [max(float(candle.high) - float(candle.low), 0.0) for candle in data.candles[-6:-1]]
    avg_range = (sum(recent_ranges) / len(recent_ranges)) if recent_ranges else 0.0
    current_range = max(current_high - current_low, 0.0)
    volatility_ok = current_range >= avg_range * MIN_VOLATILITY_FACTOR if avg_range > 0 else True

    momentum = abs(current_close - float(previous_candle.close))
    previous_momentum = abs(float(previous_candle.close) - float(prev_reference.close))
    momentum_ok = momentum >= previous_momentum * MOMENTUM_FACTOR

    volume_ratio = compute_volume_ratio(data.candles)
    volume_ok = True if volume_ratio is None else volume_ratio >= MIN_VOLUME_RATIO

    fast_timeframe = data.timeframe_minutes <= FAST_TIMEFRAME_MINUTES
    buy_close_threshold = FAST_BUY_CLOSE_POSITION if fast_timeframe else SLOW_BUY_CLOSE_POSITION
    sell_close_threshold = FAST_SELL_CLOSE_POSITION if fast_timeframe else SLOW_SELL_CLOSE_POSITION
    strong_close_buy = close_position > buy_close_threshold
    strong_close_sell = close_position < sell_close_threshold

    bullish_score = _score_setup(volatility_ok, momentum_ok, volume_ok, strong_close_buy)
    bearish_score = _score_setup(volatility_ok, momentum_ok, volume_ok, strong_close_sell)

    if trend == "bullish":
        bullish_score += 1
    elif trend == "bearish":
        bearish_score += 1

    trend_strength = 0.0
    if indicators.ema_9 is not None and indicators.ema_21 is not None:
        trend_strength = abs(float(indicators.ema_9) - float(indicators.ema_21))
    sideways = trend_strength < (SIDEWAYS_TREND_STRENGTH_PCT * price)

    signal = None
    reason = "No valid breakout setup"
    entry_price = None
    target = None
    stop_loss = None

    if not sideways:
        if bullish_break and bullish_score >= MIN_SCORE_TO_TRIGGER and bullish_score >= bearish_score:
            signal = "CALL"
            entry_price, target, stop_loss = _build_trade_levels(current_close, current_range, "CALL")
            reason = _build_reason("CALL", volatility_ok, momentum_ok, volume_ok, strong_close_buy)
        elif bearish_break and bearish_score >= MIN_SCORE_TO_TRIGGER:
            signal = "PUT"
            entry_price, target, stop_loss = _build_trade_levels(current_close, current_range, "PUT")
            reason = _build_reason("PUT", volatility_ok, momentum_ok, volume_ok, strong_close_sell)
        else:
            reason = "Breakout conditions not strong enough"
    else:
        reason = "Sideways market filter"

    return {
        "signal": signal,
        "trend": trend,
        "bullish_score": bullish_score,
        "bearish_score": bearish_score,
        "entry_price": entry_price,
        "target": target,
        "stop_loss": stop_loss,
        "reason": reason,
        "ema_9": indicators.ema_9,
        "ema_21": indicators.ema_21,
        "rsi": indicators.rsi,
        "breakout_level": breakout_level,
        "breakdown_level": breakdown_level,
        "close_position": round(close_position, 4),
        "bullish_break": bullish_break,
        "bearish_break": bearish_break,
        "volatility_ok": volatility_ok,
        "momentum_ok": momentum_ok,
        "volume_ok": volume_ok,
        "volume_ratio": volume_ratio,
        "trend_strength": round(trend_strength, 4),
        "sideways": sideways,
    }


def _score_setup(*conditions: bool) -> int:
    return sum(1 for condition in conditions if condition)


def _build_trade_levels(close_price: float, candle_range: float, signal: str) -> tuple[float, float, float]:
    effective_risk = max(candle_range, max(close_price * 0.0015, 0.5))
    if signal == "CALL":
        target = close_price + (effective_risk * TARGET_RISK_MULTIPLIER)
        stop_loss = close_price - (effective_risk * STOP_RISK_MULTIPLIER)
    else:
        target = close_price - (effective_risk * TARGET_RISK_MULTIPLIER)
        stop_loss = close_price + (effective_risk * STOP_RISK_MULTIPLIER)
    return round(close_price, 2), round(target, 2), round(stop_loss, 2)


def _build_reason(signal: str, volatility_ok: bool, momentum_ok: bool, volume_ok: bool, candle_strength_ok: bool) -> str:
    direction_text = "Breakout" if signal == "CALL" else "Breakdown"
    confirmations: list[str] = []
    if momentum_ok:
        confirmations.append("momentum")
    if volume_ok:
        confirmations.append("volume")
    if volatility_ok:
        confirmations.append("volatility")
    if candle_strength_ok:
        confirmations.append("candle_strength")
    confirmation_text = " + ".join(confirmations) if confirmations else "base trigger"
    return f"{direction_text} + {confirmation_text}"


def _empty_result(reason: str) -> dict[str, object]:
    return {
        "signal": None,
        "trend": "neutral",
        "bullish_score": 0,
        "bearish_score": 0,
        "entry_price": None,
        "target": None,
        "stop_loss": None,
        "reason": reason,
        "ema_9": None,
        "ema_21": None,
        "rsi": None,
        "breakout_level": None,
        "breakdown_level": None,
        "close_position": None,
        "bullish_break": False,
        "bearish_break": False,
        "volatility_ok": False,
        "momentum_ok": False,
        "volume_ok": False,
        "volume_ratio": None,
        "trend_strength": 0.0,
        "sideways": False,
    }

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IndicatorSnapshot:
    ema_9: float | None
    ema_21: float | None
    rsi: float | None


def calculate_ema(close_prices: list[float], period: int) -> float | None:
    if period <= 0:
        raise ValueError("EMA period must be greater than 0.")
    if len(close_prices) < period:
        return None

    multiplier = 2 / (period + 1)
    ema = sum(close_prices[:period]) / period

    for price in close_prices[period:]:
        ema = ((price - ema) * multiplier) + ema

    return ema


def calculate_rsi(close_prices: list[float], period: int = 14) -> float | None:
    if period <= 0:
        raise ValueError("RSI period must be greater than 0.")
    if len(close_prices) <= period:
        return None

    gains: list[float] = []
    losses: list[float] = []

    for current_price, previous_price in zip(close_prices[1:], close_prices[:-1]):
        change = current_price - previous_price
        gains.append(max(change, 0.0))
        losses.append(abs(min(change, 0.0)))

    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period

    for gain, loss in zip(gains[period:], losses[period:]):
        average_gain = ((average_gain * (period - 1)) + gain) / period
        average_loss = ((average_loss * (period - 1)) + loss) / period

    if average_loss == 0:
        return 100.0

    relative_strength = average_gain / average_loss
    return 100 - (100 / (1 + relative_strength))


def calculate_indicators(close_prices: list[float]) -> IndicatorSnapshot:
    return IndicatorSnapshot(
        ema_9=calculate_ema(close_prices, period=9),
        ema_21=calculate_ema(close_prices, period=21),
        rsi=calculate_rsi(close_prices, period=14),
    )


def detect_trend(ema_9: float | None, ema_21: float | None) -> str:
    if ema_9 is None or ema_21 is None:
        return "neutral"
    if ema_9 > ema_21:
        return "bullish"
    if ema_9 < ema_21:
        return "bearish"
    return "neutral"

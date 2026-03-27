from __future__ import annotations

from dataclasses import dataclass

from data.candle_store import Candle


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


def calculate_atr(candles: list[Candle], period: int = 14) -> float | None:
    if len(candles) < period + 1:
        return None

    true_ranges: list[float] = []
    for current, previous in zip(candles[1:], candles[:-1]):
        true_ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )

    atr = sum(true_ranges[:period]) / period
    for true_range in true_ranges[period:]:
        atr = ((atr * (period - 1)) + true_range) / period
    return round(atr, 2)


def calculate_adx(candles: list[Candle], period: int = 14) -> float | None:
    if len(candles) < (period * 2):
        return None

    tr_values: list[float] = []
    plus_dm_values: list[float] = []
    minus_dm_values: list[float] = []
    for current, previous in zip(candles[1:], candles[:-1]):
        up_move = current.high - previous.high
        down_move = previous.low - current.low
        plus_dm = up_move if up_move > down_move and up_move > 0 else 0.0
        minus_dm = down_move if down_move > up_move and down_move > 0 else 0.0
        tr = max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )
        tr_values.append(tr)
        plus_dm_values.append(plus_dm)
        minus_dm_values.append(minus_dm)

    smoothed_tr = sum(tr_values[:period])
    smoothed_plus_dm = sum(plus_dm_values[:period])
    smoothed_minus_dm = sum(minus_dm_values[:period])
    dx_values: list[float] = []

    for index in range(period, len(tr_values)):
        smoothed_tr = smoothed_tr - (smoothed_tr / period) + tr_values[index]
        smoothed_plus_dm = smoothed_plus_dm - (smoothed_plus_dm / period) + plus_dm_values[index]
        smoothed_minus_dm = smoothed_minus_dm - (smoothed_minus_dm / period) + minus_dm_values[index]

        if smoothed_tr <= 0:
            continue

        plus_di = (smoothed_plus_dm / smoothed_tr) * 100
        minus_di = (smoothed_minus_dm / smoothed_tr) * 100
        denominator = plus_di + minus_di
        if denominator <= 0:
            continue
        dx_values.append(abs(plus_di - minus_di) / denominator * 100)

    if len(dx_values) < period:
        return None

    adx = sum(dx_values[:period]) / period
    for dx in dx_values[period:]:
        adx = ((adx * (period - 1)) + dx) / period
    return round(adx, 2)


def calculate_vwap(candles: list[Candle]) -> float | None:
    total_volume = sum(max(candle.volume, 0) for candle in candles)
    if total_volume <= 0:
        return None
    traded_value = sum((((candle.high + candle.low + candle.close) / 3) * max(candle.volume, 0)) for candle in candles)
    return round(traded_value / total_volume, 2)


def calculate_volume_average(candles: list[Candle], period: int = 10) -> float | None:
    if len(candles) < period:
        return None
    window = candles[-period:]
    return round(sum(max(candle.volume, 0) for candle in window) / period, 2)


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

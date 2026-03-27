from __future__ import annotations

from dataclasses import dataclass

from data.candle_store import Candle
from strategy.indicators import calculate_adx, calculate_atr, calculate_vwap, calculate_volume_average


@dataclass(frozen=True)
class MarketRegimeSnapshot:
    regime: str
    adx: float | None
    atr: float | None
    atr_average: float | None
    range_pct: float
    vwap: float | None
    avg_volume: float | None
    volume_spike_ratio: float | None


def detect_market_regime(
    candles: list[Candle],
    adx_trending_threshold: float = 25.0,
    adx_sideways_threshold: float = 20.0,
    atr_spike_multiplier: float = 1.5,
    range_compression_threshold_pct: float = 0.012,
) -> MarketRegimeSnapshot:
    if len(candles) < 20:
        return MarketRegimeSnapshot(
            regime="UNKNOWN",
            adx=None,
            atr=None,
            atr_average=None,
            range_pct=0.0,
            vwap=calculate_vwap(candles),
            avg_volume=calculate_volume_average(candles),
            volume_spike_ratio=None,
        )

    adx = calculate_adx(candles, period=14)
    atr = calculate_atr(candles, period=14)
    recent_atrs = []
    for index in range(14, len(candles) + 1):
        value = calculate_atr(candles[:index], period=14)
        if value is not None:
            recent_atrs.append(value)
    atr_average = (sum(recent_atrs[-10:]) / min(len(recent_atrs[-10:]), 10)) if recent_atrs else None

    recent_window = candles[-20:]
    highest_high = max(candle.high for candle in recent_window)
    lowest_low = min(candle.low for candle in recent_window)
    last_close = recent_window[-1].close or 1.0
    range_pct = (highest_high - lowest_low) / last_close if last_close else 0.0
    tight_range = range_pct <= range_compression_threshold_pct

    regime = "TRENDING"
    if atr is not None and atr_average is not None and atr > (atr_average * atr_spike_multiplier):
        regime = "VOLATILE"
    elif adx is not None and adx < adx_sideways_threshold and tight_range:
        regime = "SIDEWAYS"
    elif adx is None or adx <= adx_trending_threshold:
        regime = "SIDEWAYS" if tight_range else "VOLATILE"

    avg_volume = calculate_volume_average(candles)
    current_volume = float(candles[-1].volume or 0)
    volume_spike_ratio = None if not avg_volume or avg_volume <= 0 else round(current_volume / avg_volume, 2)

    return MarketRegimeSnapshot(
        regime=regime,
        adx=adx,
        atr=atr,
        atr_average=atr_average,
        range_pct=round(range_pct, 4),
        vwap=calculate_vwap(candles),
        avg_volume=avg_volume,
        volume_spike_ratio=volume_spike_ratio,
    )

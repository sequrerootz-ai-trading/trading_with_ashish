from __future__ import annotations


def compute_close_position(high_price: float, low_price: float, close_price: float) -> float:
    candle_range = max(high_price - low_price, 0.0)
    if candle_range == 0:
        return 0.5
    return (close_price - low_price) / candle_range


def compute_volume_ratio(candles: list[object], lookback: int = 10) -> float | None:
    if len(candles) < 2:
        return None
    sample = candles[-(lookback + 1):-1]
    baseline = [float(getattr(candle, "volume", 0) or 0.0) for candle in sample]
    current_volume = float(getattr(candles[-1], "volume", 0) or 0.0)
    average_volume = (sum(baseline) / len(baseline)) if baseline else 0.0
    if average_volume <= 0:
        return None
    return round(current_volume / average_volume, 2)


def premium_trade_levels(entry_price: float, target_pct: float = 0.20, stop_loss_pct: float = 0.15) -> dict[str, float]:
    if entry_price <= 0:
        raise ValueError("entry_price must be greater than 0")
    return {
        "entry_price": round(entry_price, 2),
        "target": round(entry_price * (1 + target_pct), 2),
        "stop_loss": round(entry_price * (1 - stop_loss_pct), 2),
    }

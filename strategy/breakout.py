from __future__ import annotations

from dataclasses import dataclass

from data.candle_store import Candle


@dataclass(frozen=True)
class BreakoutResult:
    valid_breakout: bool
    fake: bool
    strength: str
    reason: str


def detect_fast_breakout(
    current_candle: Candle,
    previous_candle: Candle,
    avg_volume: float,
    next_candle: Candle | None = None,
) -> BreakoutResult:
    candle_range = max(current_candle.high - current_candle.low, 0.0)
    close_position = 0.0
    if candle_range > 0:
        close_position = (current_candle.close - current_candle.low) / candle_range

    breakout = current_candle.high > previous_candle.high
    volume_ok = current_candle.volume > (1.3 * avg_volume)
    close_strong = close_position > 0.60
    reversal = _is_reversal_after_breakout(current_candle, previous_candle, next_candle)

    if not breakout:
        return BreakoutResult(
            valid_breakout=False,
            fake=False,
            strength="weak",
            reason="no_breakout",
        )

    if volume_ok and close_strong and not reversal:
        strength = "strong" if close_position >= 0.80 else "weak"
        return BreakoutResult(
            valid_breakout=True,
            fake=False,
            strength=strength,
            reason="high_breakout_with_volume_and_strong_close",
        )

    reason_parts: list[str] = []
    if not volume_ok:
        reason_parts.append("low_volume")
    if not close_strong:
        reason_parts.append("weak_close")
    if reversal:
        reason_parts.append("next_candle_reversal")

    return BreakoutResult(
        valid_breakout=False,
        fake=True,
        strength="weak",
        reason=",".join(reason_parts) or "fake_breakout",
    )


def _is_reversal_after_breakout(
    current_candle: Candle,
    previous_candle: Candle,
    next_candle: Candle | None,
) -> bool:
    if next_candle is None:
        return False
    return (
        next_candle.close < current_candle.low
        or next_candle.close < previous_candle.high
    )

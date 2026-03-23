from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from data.candle_store import Candle
from data.option_premium import PremiumQuote
from strategy.indicators import calculate_indicators, detect_trend


@dataclass(frozen=True)
class SignalEvent:
    symbol: str
    candle_close: str
    signal: str | None
    reason: str
    should_trade: bool
    confidence_score: float
    premium_symbol: str | None = None
    premium_price: float | None = None
    strength: str = "weak"


class CandleCloseSignalStrategy:
    def __init__(
        self,
        candle_fetcher: Callable[[str], list[Candle]],
        premium_fetcher: Callable[[str, float, str], PremiumQuote | None] | None = None,
    ) -> None:
        self.candle_fetcher = candle_fetcher
        self.premium_fetcher = premium_fetcher
        self._last_signal_by_symbol: dict[str, str | None] = {}
        self._active_trade_by_symbol: dict[str, bool] = {}

    def on_candle(self, candle: Candle) -> SignalEvent:
        closed_candles = self.candle_fetcher(candle.symbol)
        close_prices = [item.close for item in closed_candles]
        indicators = calculate_indicators(close_prices)
        trend = detect_trend(indicators.ema_9, indicators.ema_21)
        previous_candle = closed_candles[-2] if len(closed_candles) >= 2 else None
        avg_volume = self._average_volume(closed_candles[-10:])

        signal, strength, confidence_score, pattern_reason = self._build_strong_signal(
            candle,
            previous_candle,
            trend,
            indicators.rsi,
            avg_volume,
        )

        reason_parts = [
            f"ema9={_fmt(indicators.ema_9)}",
            f"ema21={_fmt(indicators.ema_21)}",
            f"rsi={_fmt(indicators.rsi)}",
            f"trend={trend}",
            pattern_reason,
        ]

        should_trade = False
        if signal is None:
            reason_parts.append("no_strong_signal")
        elif self._active_trade_by_symbol.get(candle.symbol, False):
            reason_parts.append("active_trade_exists")
        elif self._last_signal_by_symbol.get(candle.symbol) == signal:
            reason_parts.append("duplicate_signal_skipped")
        else:
            should_trade = True
            self._last_signal_by_symbol[candle.symbol] = signal
            self._active_trade_by_symbol[candle.symbol] = True
            reason_parts.append("trade_allowed")

        premium_symbol = None
        premium_price = None
        if should_trade and self.premium_fetcher is not None and signal is not None:
            try:
                premium_quote = self.premium_fetcher(candle.symbol, candle.close, signal)
            except Exception:
                premium_quote = None
                reason_parts.append("premium_lookup_failed")
            if premium_quote is not None:
                premium_symbol = premium_quote.trading_symbol
                premium_price = premium_quote.last_price
                reason_parts.append("premium_resolved")

        reason = " ".join(reason_parts)

        if should_trade and signal is not None:
            signal_text = f"[STRONG {signal}] {candle.symbol}"
            if premium_symbol and premium_price is not None:
                signal_text += f" | premium={premium_symbol} @ {premium_price:.2f}"
            else:
                signal_text += f" | spot={candle.close:.2f}"
            signal_text += (
                f" | confidence={confidence_score:.2f}"
                f" | candle_close={candle.end:%Y-%m-%d %H:%M}"
            )
            print(signal_text)
            print(f"[REASON] {reason}")

        return SignalEvent(
            symbol=candle.symbol,
            candle_close=f"{candle.end:%Y-%m-%d %H:%M}",
            signal=signal,
            reason=reason,
            should_trade=should_trade,
            confidence_score=confidence_score if should_trade else 0.0,
            premium_symbol=premium_symbol,
            premium_price=premium_price,
            strength=strength,
        )

    def mark_trade_closed(self, symbol: str) -> None:
        self._active_trade_by_symbol[symbol] = False

    def has_active_trade(self, symbol: str) -> bool:
        return self._active_trade_by_symbol.get(symbol, False)

    def _build_strong_signal(
        self,
        current_candle: Candle,
        previous_candle: Candle | None,
        trend: str,
        rsi: float | None,
        avg_volume: float,
    ) -> tuple[str | None, str, float, str]:
        if previous_candle is None or avg_volume <= 0:
            return None, "weak", 0.0, "insufficient_history"

        candle_range = max(current_candle.high - current_candle.low, 0.0)
        close_position = 0.5 if candle_range == 0 else (current_candle.close - current_candle.low) / candle_range
        volume_ok = current_candle.volume > (1.3 * avg_volume)

        bullish_breakout = current_candle.high > previous_candle.high and close_position > 0.60
        bearish_breakdown = current_candle.low < previous_candle.low and close_position < 0.40
        bullish_rsi = rsi is not None and rsi >= 55
        bearish_rsi = rsi is not None and rsi <= 45

        if trend == "bullish" and bullish_breakout and volume_ok and bullish_rsi:
            confidence = 0.75
            if close_position >= 0.80:
                confidence += 0.15
            if current_candle.close > previous_candle.close:
                confidence += 0.10
            return "CALL", "strong", min(confidence, 1.0), "bullish_breakout_confirmed"

        if trend == "bearish" and bearish_breakdown and volume_ok and bearish_rsi:
            confidence = 0.75
            if close_position <= 0.20:
                confidence += 0.15
            if current_candle.close < previous_candle.close:
                confidence += 0.10
            return "PUT", "strong", min(confidence, 1.0), "bearish_breakdown_confirmed"

        if trend == "bullish" and bullish_breakout:
            return None, "weak", 0.0, "bullish_breakout_but_not_strong"
        if trend == "bearish" and bearish_breakdown:
            return None, "weak", 0.0, "bearish_breakdown_but_not_strong"
        return None, "weak", 0.0, "no_directional_breakout"

    @staticmethod
    def _average_volume(candles: list[Candle]) -> float:
        if not candles:
            return 0.0
        return sum(candle.volume for candle in candles) / len(candles)


def _fmt(value: float | None) -> str:
    if value is None:
        return "NA"
    return f"{value:.2f}"

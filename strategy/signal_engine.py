from __future__ import annotations

from dataclasses import dataclass

from data.candle_store import Candle
from data.database import TradingDatabase
from strategy.indicators import calculate_indicators, detect_trend


@dataclass(frozen=True)
class SignalContext:
    symbol: str
    candles: list[Candle]
    last_candle: Candle | None


@dataclass(frozen=True)
class GeneratedSignal:
    symbol: str
    timestamp: str
    signal: str
    reason: str
    confidence: float


def get_last_closed_candle(symbol: str, database: TradingDatabase) -> Candle | None:
    return database.get_last_closed_candle(symbol)


def store_market_data(data: Candle, database: TradingDatabase) -> bool:
    return database.store_market_data(data)


def fetch_or_get_cached_news(symbol: str, news_service) -> list[str]:
    return news_service.fetch_or_get_cached_news(symbol)


def get_sentiment_with_cache(symbol: str, headlines: list[str], news_service) -> dict[str, object]:
    return news_service.get_sentiment_with_cache(symbol, headlines)


def generate_signal(symbol: str, data: SignalContext, sentiment: dict[str, object]) -> GeneratedSignal:
    if data.last_candle is None or len(data.candles) < 2:
        return GeneratedSignal(symbol=symbol, timestamp="", signal="NO_TRADE", reason="insufficient_closed_candles", confidence=0.0)

    close_prices = [candle.close for candle in data.candles]
    indicators = calculate_indicators(close_prices)
    trend = detect_trend(indicators.ema_9, indicators.ema_21)
    current_candle = data.last_candle
    previous_candle = data.candles[-2]
    avg_volume = _average_volume(data.candles[-10:])

    candle_range = max(current_candle.high - current_candle.low, 0.0)
    close_position = 0.5 if candle_range == 0 else (current_candle.close - current_candle.low) / candle_range
    volume_ok = current_candle.volume > (1.3 * avg_volume) if avg_volume > 0 else False

    technical_signal = "NO_TRADE"
    reason = []
    confidence = 0.0

    if trend == "bullish" and current_candle.high > previous_candle.high and close_position > 0.60 and volume_ok:
        technical_signal = "BUY_CE"
        confidence = 0.8
        reason.append("bullish_breakout")
    elif trend == "bearish" and current_candle.low < previous_candle.low and close_position < 0.40 and volume_ok:
        technical_signal = "BUY_PE"
        confidence = 0.8
        reason.append("bearish_breakdown")
    else:
        reason.append("technical_filter_not_met")

    sentiment_label = str(sentiment.get("sentiment", "SIDEWAYS")).upper()
    if technical_signal == "BUY_CE" and sentiment_label != "BULLISH":
        technical_signal = "NO_TRADE"
        confidence = 0.0
        reason.append(f"sentiment_mismatch={sentiment_label}")
    elif technical_signal == "BUY_PE" and sentiment_label != "BEARISH":
        technical_signal = "NO_TRADE"
        confidence = 0.0
        reason.append(f"sentiment_mismatch={sentiment_label}")
    elif technical_signal != "NO_TRADE":
        confidence = min(1.0, confidence + float(sentiment.get("confidence", 0.0)) * 0.2)
        reason.append(f"sentiment_confirmed={sentiment_label}")

    reason.extend([
        f"ema9={_fmt(indicators.ema_9)}",
        f"ema21={_fmt(indicators.ema_21)}",
        f"rsi={_fmt(indicators.rsi)}",
        f"trend={trend}",
    ])

    return GeneratedSignal(symbol=symbol, timestamp=current_candle.end.isoformat(), signal=technical_signal, reason=" ".join(reason), confidence=confidence)


def store_signal(signal: GeneratedSignal, database: TradingDatabase) -> None:
    database.store_signal(signal.symbol, signal.timestamp or "", signal.signal, signal.reason)


def _average_volume(candles: list[Candle]) -> float:
    if not candles:
        return 0.0
    return sum(candle.volume for candle in candles) / len(candles)


def _fmt(value: float | None) -> str:
    if value is None:
        return "NA"
    return f"{value:.2f}"

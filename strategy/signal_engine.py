from __future__ import annotations

from config import get_market_type
from data.candle_store import Candle
from data.database import TradingDatabase
from strategy.signal_types import GeneratedSignal, SignalContext
from strategy.strategy_equity import generate_equity_signal
from strategy.strategy_mcx import generate_mcx_signal


def get_last_closed_candle(symbol: str, database: TradingDatabase) -> Candle | None:
    return database.get_last_closed_candle(symbol)


def store_market_data(data: Candle, database: TradingDatabase) -> bool:
    return database.store_market_data(data)


def fetch_or_get_cached_news(symbol: str, news_service) -> list[str]:
    return news_service.fetch_or_get_cached_news(symbol)


def get_sentiment_with_cache(symbol: str, headlines: list[str], news_service) -> dict[str, object]:
    return news_service.get_sentiment_with_cache(symbol, headlines)


def generate_signal(
    symbol: str,
    market_type: str,
    data: SignalContext,
    sentiment: dict[str, object] | None = None,
) -> GeneratedSignal:
    normalized_market_type = market_type.strip().upper() if market_type else get_market_type()
    if normalized_market_type == "MCX":
        return generate_mcx_signal(symbol, data)
    return generate_equity_signal(symbol, data, sentiment or _default_sentiment())


def store_signal(signal: GeneratedSignal, database: TradingDatabase) -> None:
    database.store_signal(signal.symbol, signal.timestamp or "", signal.signal, signal.reason)


def _default_sentiment() -> dict[str, object]:
    return {
        "sentiment": "SIDEWAYS",
        "confidence": 0.0,
        "reason": "sentiment_disabled",
    }

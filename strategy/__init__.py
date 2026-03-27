"""Trading strategy package."""

from strategy.ai_sentiment import AISentimentAnalyzer, SentimentResult
from strategy.breakout import BreakoutResult, detect_fast_breakout
from strategy.indicators import (
    IndicatorSnapshot,
    calculate_ema,
    calculate_indicators,
    calculate_rsi,
    detect_trend,
)
from strategy.market_sentiment import MarketSentiment, get_market_sentiment
from strategy.signal_engine import (
    fetch_or_get_cached_news,
    generate_signal,
    get_last_closed_candle,
    get_sentiment_with_cache,
    store_market_data,
    store_signal,
)
from strategy.signal_generator import FinalSignal, generate_final_signal
from strategy.signal_types import GeneratedSignal, SignalContext
from strategy.strategy import LastClosedCandleStrategy
from strategy.strategy_equity import generate_equity_signal
from strategy.strategy_mcx import generate_mcx_signal

__all__ = [
    "AISentimentAnalyzer",
    "BreakoutResult",
    "FinalSignal",
    "GeneratedSignal",
    "IndicatorSnapshot",
    "LastClosedCandleStrategy",
    "MarketSentiment",
    "SentimentResult",
    "SignalContext",
    "calculate_ema",
    "calculate_indicators",
    "calculate_rsi",
    "detect_fast_breakout",
    "detect_trend",
    "fetch_or_get_cached_news",
    "generate_equity_signal",
    "generate_final_signal",
    "generate_mcx_signal",
    "generate_signal",
    "get_last_closed_candle",
    "get_market_sentiment",
    "get_sentiment_with_cache",
    "store_market_data",
    "store_signal",
]

from __future__ import annotations

from config import get_market_type
from data.candle_store import Candle
from data.database import TradingDatabase
from strategy.equity_signal_engine import (
    generate_equity_signal_engine,
    get_last_closed_candle,
    store_market_data,
    store_signal,
)
from strategy.mcx_signal_engine import generate_mcx_signal_engine
from strategy.signal_types import GeneratedSignal, SignalContext


def generate_signal(
    symbol: str,
    market_type: str,
    data: SignalContext,
    sentiment: dict[str, object] | None = None,
    max_trades_per_day: int = 10,
) -> GeneratedSignal:
    normalized_market_type = (market_type or get_market_type()).strip().upper()
    if normalized_market_type == "MCX":
        return generate_mcx_signal_engine(symbol, data, sentiment=sentiment, max_trades_per_day=max_trades_per_day)
    return generate_equity_signal_engine(symbol, data, sentiment=sentiment, max_trades_per_day=max_trades_per_day)


__all__ = [
    "generate_signal",
    "get_last_closed_candle",
    "store_market_data",
    "store_signal",
]

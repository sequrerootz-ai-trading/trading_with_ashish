"""Trading strategy package."""

from strategy.breakout import BreakoutResult, detect_fast_breakout
from strategy.indicators import (
    IndicatorSnapshot,
    calculate_ema,
    calculate_indicators,
    calculate_rsi,
    detect_trend,
)
from strategy.signal_engine import (
    generate_signal,
    get_last_closed_candle,
    store_market_data,
    store_signal,
)
from strategy.signal_generator import FinalSignal, generate_final_signal
from strategy.nifty_options import generate_nifty_hybrid_signal, generate_nifty_options_signal
from strategy.signal_types import GeneratedSignal, SignalContext
from strategy.strategy import LastClosedCandleStrategy
from strategy.strategy_equity import generate_equity_signal
from strategy.strategy_mcx import generate_mcx_signal

__all__ = [
    "BreakoutResult",
    "FinalSignal",
    "GeneratedSignal",
    "IndicatorSnapshot",
    "LastClosedCandleStrategy",
    "SignalContext",
    "calculate_ema",
    "calculate_indicators",
    "calculate_rsi",
    "detect_fast_breakout",
    "detect_trend",
    "generate_equity_signal",
    "generate_final_signal",
    "generate_nifty_hybrid_signal",
    "generate_nifty_options_signal",
    "generate_mcx_signal",
    "generate_signal",
    "get_last_closed_candle",
    "store_market_data",
    "store_signal",
]

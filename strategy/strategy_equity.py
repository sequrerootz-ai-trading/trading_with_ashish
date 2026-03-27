from __future__ import annotations

from strategy.equity_decision_engine import build_equity_decision
from strategy.signal_types import GeneratedSignal, SignalContext


def generate_equity_signal(
    symbol: str,
    data: SignalContext,
    sentiment: dict[str, object],
) -> GeneratedSignal:
    _ = sentiment
    return build_equity_decision(symbol, data)

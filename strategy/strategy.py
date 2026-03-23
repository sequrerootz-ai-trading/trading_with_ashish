from __future__ import annotations

import logging

from data.candle_manager import CandleManager
from strategy.signal_engine import GeneratedSignal, SignalContext, generate_signal


logger = logging.getLogger(__name__)
MINIMUM_INDICATOR_CANDLES = 21


class LastClosedCandleStrategy:
    def __init__(self, candle_manager: CandleManager, symbol: str) -> None:
        self.candle_manager = candle_manager
        self.symbol = symbol

    def evaluate(self, sentiment: dict[str, object]) -> GeneratedSignal | None:
        if not self.candle_manager.has_sufficient_data(self.symbol, MINIMUM_INDICATOR_CANDLES):
            logger.warning("[INFO] Processing SYMBOL: %s | Waiting for sufficient data", self.symbol)
            return None

        candles = self.candle_manager.get_closed_candles(self.symbol)
        last_completed = self.candle_manager.get_last_completed_candle(self.symbol)
        if last_completed is None:
            logger.warning("[INFO] Processing SYMBOL: %s | Waiting for sufficient data", self.symbol)
            return None

        return generate_signal(
            self.symbol,
            SignalContext(symbol=self.symbol, candles=candles, last_candle=last_completed),
            sentiment,
        )

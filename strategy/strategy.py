from __future__ import annotations

import logging

from data.candle_manager import CandleManager
from strategy.signal_engine import generate_signal
from strategy.signal_types import GeneratedSignal, SignalContext


logger = logging.getLogger(__name__)
MINIMUM_INDICATOR_CANDLES = 21


class LastClosedCandleStrategy:
    def __init__(
        self,
        candle_manager: CandleManager,
        symbol: str,
        market_type: str,
        timeframe_minutes: int = 5,
    ) -> None:
        self.candle_manager = candle_manager
        self.symbol = symbol
        self.market_type = market_type
        self.timeframe_minutes = timeframe_minutes

    def evaluate(self, sentiment: dict[str, object]) -> GeneratedSignal | None:
        candle_count = len(self.candle_manager.get_closed_candles(self.symbol))
        if candle_count < MINIMUM_INDICATOR_CANDLES:
            logger.warning(
                "[INFO] Processing SYMBOL: %s | Waiting for sufficient data | candles=%s/%s",
                self.symbol,
                candle_count,
                MINIMUM_INDICATOR_CANDLES,
            )
            return None

        candles = self.candle_manager.get_closed_candles(self.symbol)
        last_completed = self.candle_manager.get_last_completed_candle(self.symbol)
        if last_completed is None:
            logger.warning("[INFO] Processing SYMBOL: %s | Waiting for sufficient data | no closed candle", self.symbol)
            return None

        return generate_signal(
            self.symbol,
            self.market_type,
            SignalContext(
                symbol=self.symbol,
                candles=candles,
                last_candle=last_completed,
                timeframe_minutes=self.timeframe_minutes,
            ),
            sentiment,
        )

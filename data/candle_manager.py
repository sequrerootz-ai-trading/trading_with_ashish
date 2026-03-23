from __future__ import annotations

from collections import defaultdict, deque

from data.candle_store import Candle


class CandleManager:
    def __init__(self, max_candles: int = 100) -> None:
        self.max_candles = max_candles
        self._closed_candles: dict[str, deque[Candle]] = defaultdict(
            lambda: deque(maxlen=max_candles)
        )

    def initialize_candles(self, symbol: str, candles: list[Candle]) -> None:
        buffer = self._closed_candles[symbol]
        buffer.clear()
        for candle in candles[-self.max_candles :]:
            buffer.append(candle)

    def append_closed_candle(self, candle: Candle) -> None:
        self._closed_candles[candle.symbol].append(candle)

    def on_new_closed_candle(self, candle: Candle) -> None:
        self.append_closed_candle(candle)

    def get_closed_candles(self, symbol: str) -> list[Candle]:
        return list(self._closed_candles.get(symbol, []))

    def get_last_completed_candle(self, symbol: str) -> Candle | None:
        candles = self._closed_candles.get(symbol)
        if not candles:
            return None
        return candles[-1]

    def has_sufficient_data(self, symbol: str, minimum: int) -> bool:
        return len(self._closed_candles.get(symbol, [])) >= minimum

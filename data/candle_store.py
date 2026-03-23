from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class Candle:
    symbol: str
    start: datetime
    end: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0


class CandleAggregator:
    def __init__(self, timeframe_minutes: int = 5, max_candles: int = 200) -> None:
        self.timeframe_minutes = timeframe_minutes
        self._active: dict[str, Candle] = {}
        self._candles: dict[str, deque[Candle]] = defaultdict(
            lambda: deque(maxlen=max_candles)
        )

    def update(
        self,
        symbol: str,
        price: float,
        tick_time: datetime,
        volume_increment: int = 0,
    ) -> Candle | None:
        bucket_start = self._bucket_start(tick_time)
        bucket_end = bucket_start + timedelta(minutes=self.timeframe_minutes)
        active_candle = self._active.get(symbol)

        if active_candle is None:
            self._active[symbol] = Candle(
                symbol=symbol,
                start=bucket_start,
                end=bucket_end,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=max(volume_increment, 0),
            )
            return None

        if bucket_start > active_candle.start:
            closed_candle = active_candle
            self._candles[symbol].append(closed_candle)
            self._active[symbol] = Candle(
                symbol=symbol,
                start=bucket_start,
                end=bucket_end,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=max(volume_increment, 0),
            )
            return closed_candle

        active_candle.high = max(active_candle.high, price)
        active_candle.low = min(active_candle.low, price)
        active_candle.close = price
        active_candle.volume += max(volume_increment, 0)
        return None

    def get_candles(self, symbol: str) -> list[Candle]:
        candles = list(self._candles.get(symbol, []))
        active = self._active.get(symbol)
        if active is not None:
            candles.append(active)
        return candles

    def get_closed_candles(self, symbol: str) -> list[Candle]:
        return list(self._candles.get(symbol, []))

    def _bucket_start(self, tick_time: datetime) -> datetime:
        minute_bucket = tick_time.minute - (tick_time.minute % self.timeframe_minutes)
        return tick_time.replace(
            minute=minute_bucket,
            second=0,
            microsecond=0,
        )

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from data.candle_store import Candle
from data.database import TradingDatabase
from data.market_data import MarketDataService


logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 15
MINIMUM_STARTUP_CANDLES = 50
MAX_STARTUP_CANDLES = 100
HISTORICAL_RETRIES = 3


@dataclass(frozen=True)
class HistoricalLoadResult:
    symbol: str
    candles: list[Candle]
    source: str


class HistoricalDataLoader:
    def __init__(self, market_data: MarketDataService, database: TradingDatabase) -> None:
        self.market_data = market_data
        self.database = database

    def fetch_historical_candles(self, symbol: str) -> list[Candle]:
        instrument = self.market_data.get_resolved_instrument(symbol)
        session_start, session_end = session_window_ist()

        try:
            cached = self.database.get_market_data_range(
                symbol=symbol,
                start=session_start,
                end=session_end,
                limit=MAX_STARTUP_CANDLES,
            )
            if cached and self._covers_session(cached, session_end):
                logger.info(
                    "Using cached historical candles for %s | count=%s",
                    symbol,
                    len(cached),
                )
                return cached[-MAX_STARTUP_CANDLES:]
        except Exception as exc:
            logger.warning("Historical DB read failed for %s: %s", symbol, exc)

        last_error: Exception | None = None
        for attempt in range(1, HISTORICAL_RETRIES + 1):
            try:
                rows = self.market_data.clients.kite.historical_data(
                    instrument_token=instrument.instrument_token,
                    from_date=session_start,
                    to_date=session_end,
                    interval="5minute",
                    continuous=False,
                    oi=False,
                )
                candles = [_row_to_candle(symbol, row) for row in rows]
                for candle in candles:
                    try:
                        self.database.store_market_data(candle)
                    except Exception as db_exc:
                        logger.warning("Historical candle store failed for %s: %s", symbol, db_exc)
                        break
                logger.info(
                    "Fetched historical candles for %s | count=%s | attempt=%s",
                    symbol,
                    len(candles),
                    attempt,
                )
                return candles[-MAX_STARTUP_CANDLES:]
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Historical fetch failed for %s on attempt %s/%s: %s",
                    symbol,
                    attempt,
                    HISTORICAL_RETRIES,
                    exc,
                )

        if last_error is not None:
            logger.warning("Historical fetch exhausted for %s, using partial cache if available.", symbol)

        try:
            cached = self.database.get_market_data_range(
                symbol=symbol,
                start=session_start,
                end=session_end,
                limit=MAX_STARTUP_CANDLES,
            )
            return cached[-MAX_STARTUP_CANDLES:]
        except Exception:
            return []

    def initialize_candles(self) -> list[HistoricalLoadResult]:
        results: list[HistoricalLoadResult] = []
        for instrument in self.market_data.resolved_instruments:
            candles = self.fetch_historical_candles(instrument.label)
            source = "historical_or_cache" if candles else "empty"
            results.append(
                HistoricalLoadResult(
                    symbol=instrument.label,
                    candles=candles,
                    source=source,
                )
            )
        return results

    @staticmethod
    def _covers_session(candles: list[Candle], session_end: datetime) -> bool:
        if len(candles) < 2:
            return False
        return candles[-1].end >= session_end


def session_window_ist(now: datetime | None = None) -> tuple[datetime, datetime]:
    current_ist = (now or datetime.now(IST)).astimezone(IST)
    session_start = current_ist.replace(
        hour=MARKET_OPEN_HOUR,
        minute=MARKET_OPEN_MINUTE,
        second=0,
        microsecond=0,
    )
    last_completed_end = round_down_to_last_completed_5m(current_ist)
    return session_start.astimezone(UTC).replace(tzinfo=None), last_completed_end.astimezone(UTC).replace(tzinfo=None)


def round_down_to_last_completed_5m(current_time: datetime) -> datetime:
    minute_bucket = current_time.minute - (current_time.minute % 5)
    bucket_end = current_time.replace(minute=minute_bucket, second=0, microsecond=0)
    if bucket_end == current_time.replace(second=0, microsecond=0):
        bucket_end -= timedelta(minutes=5)
    return bucket_end


def _row_to_candle(symbol: str, row: dict) -> Candle:
    end = row["date"]
    if getattr(end, "tzinfo", None) is not None:
        end = end.astimezone(UTC).replace(tzinfo=None)
    start = end - timedelta(minutes=5)
    return Candle(
        symbol=symbol,
        start=start,
        end=end,
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=int(float(row.get("volume", 0))),
    )

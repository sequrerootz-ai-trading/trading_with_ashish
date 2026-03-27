from __future__ import annotations

import logging
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from kiteconnect import KiteTicker

from config import get_mode
from config.settings import InstrumentConfig, Settings
from data.candle_store import Candle, CandleAggregator
from data.kite_client import KiteClients
from market_selector import resolve_instrument_selection
from utils_console import YELLOW, colorize


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedInstrument:
    label: str
    exchange: str
    tradingsymbol: str
    instrument_token: int


class MarketDataService:
    def __init__(
        self,
        settings: Settings,
        on_candle: Callable[[Candle], None] | None = None,
    ) -> None:
        self.settings = settings
        self.clients = KiteClients(settings.api_key, settings.access_token)
        self.aggregator = CandleAggregator(
            timeframe_minutes=settings.candle_interval_minutes,
            max_candles=settings.max_candles_in_memory,
        )
        self.on_candle = on_candle or self._default_on_candle
        self._last_volumes: dict[str, int] = {}
        self._token_to_symbol: dict[int, str] = {}
        self._latest_ltp: dict[str, float] = {}
        self._last_tick_at: datetime | None = None
        self._last_closed_candle_at: datetime | None = None
        self._heartbeat_started = False
        self._countdown_started = False
        self._resolved_instruments = self._resolve_instruments(settings.instruments)

    @property
    def candle_store(self) -> CandleAggregator:
        return self.aggregator

    @property
    def resolved_instruments(self) -> list[ResolvedInstrument]:
        return list(self._resolved_instruments)

    def get_resolved_instrument(self, symbol: str) -> ResolvedInstrument:
        for instrument in self._resolved_instruments:
            if instrument.label == symbol:
                return instrument
        raise ValueError(f"Resolved instrument not found for symbol={symbol}")

    def start(self) -> None:
        ticker = self.clients.ticker
        ticker.on_connect = self._on_connect
        ticker.on_ticks = self._on_ticks
        ticker.on_close = self._on_close
        ticker.on_error = self._on_error
        ticker.on_reconnect = self._on_reconnect
        ticker.on_noreconnect = self._on_noreconnect

        logger.info("[%s] Starting KiteTicker stream...", get_mode())
        self._start_heartbeat()
        self._start_countdown()
        ticker.connect(threaded=False)

    def stop(self) -> None:
        self.clients.ticker.close()

    def latest_ltp(self) -> dict[str, float]:
        return dict(self._latest_ltp)

    def get_latest_price(self, symbol: str) -> float | None:
        return self._latest_ltp.get(symbol)

    def _resolve_instruments(
        self,
        instrument_configs: list[InstrumentConfig],
    ) -> list[ResolvedInstrument]:
        resolved: list[ResolvedInstrument] = []
        for instrument in instrument_configs:
            selection = resolve_instrument_selection(
                symbol=instrument.label,
                market_type=self.settings.market_type,
                kite=self.clients.kite,
                settings=self.settings,
            )
            resolved_instrument = ResolvedInstrument(
                label=instrument.label,
                exchange=selection.exchange,
                tradingsymbol=selection.tradingsymbol,
                instrument_token=selection.instrument_token,
            )
            resolved.append(resolved_instrument)
            self._token_to_symbol[resolved_instrument.instrument_token] = resolved_instrument.label

        return resolved

    def _on_connect(self, ws: KiteTicker, response: dict) -> None:
        tokens = [instrument.instrument_token for instrument in self._resolved_instruments]
        symbol = self._resolved_instruments[0].label
        mode = get_mode()
        logger.info("[%s] Connected. Subscribing to instruments: %s", mode, tokens)
        ws.subscribe(tokens)
        ws.set_mode(ws.MODE_FULL, tokens)
        logger.info(
            "[%s] Feed active for %s | timeframe=%s min",
            mode,
            symbol,
            self.settings.candle_interval_minutes,
        )
        print(
            colorize(
                f"[{mode} WAIT] Feed active for {symbol} | waiting for candle close signal...",
                YELLOW,
                bold=True,
            )
        )

    def _on_ticks(self, ws: KiteTicker, ticks: list[dict]) -> None:
        for tick in ticks:
            token = int(tick["instrument_token"])
            symbol = self._token_to_symbol.get(token)
            if symbol is None:
                continue

            last_price = float(tick["last_price"])
            tick_time = tick.get("exchange_timestamp") or tick.get("timestamp")
            if tick_time is None:
                tick_time = datetime.now()

            self._latest_ltp[symbol] = last_price
            self._last_tick_at = datetime.now()
            logger.debug("LTP %s: %.2f", symbol, last_price)

            last_traded_quantity = int(tick.get("last_traded_quantity") or 0)
            current_volume = int(tick.get("volume_traded") or 0)
            volume_increment = self._volume_increment(symbol, current_volume, last_traded_quantity)

            closed_candle = self.aggregator.update(
                symbol=symbol,
                price=last_price,
                tick_time=tick_time,
                volume_increment=volume_increment,
            )
            if closed_candle is not None:
                self._last_closed_candle_at = datetime.now()
                logger.info(
                    "Candle closed for %s at %s | evaluating signal...",
                    closed_candle.symbol,
                    closed_candle.end.strftime("%Y-%m-%d %H:%M"),
                )
                self.on_candle(closed_candle)

    def _on_close(self, ws: KiteTicker, code: int, reason: str) -> None:
        logger.warning("WebSocket closed: code=%s reason=%s", code, reason)

    def _on_error(self, ws: KiteTicker, code: int, reason: str) -> None:
        logger.error("WebSocket error: code=%s reason=%s", code, reason)

    def _on_reconnect(self, ws: KiteTicker, attempts_count: int) -> None:
        logger.warning("Reconnecting WebSocket, attempt=%s", attempts_count)

    def _on_noreconnect(self, ws: KiteTicker) -> None:
        logger.error("WebSocket stopped reconnecting.")

    def _start_heartbeat(self) -> None:
        if self._heartbeat_started:
            return
        self._heartbeat_started = True
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()

    def _heartbeat_loop(self) -> None:
        while True:
            time.sleep(30)

    def _start_countdown(self) -> None:
        if self._countdown_started:
            return
        self._countdown_started = True
        threading.Thread(target=self._countdown_loop, daemon=True).start()

    def _countdown_loop(self) -> None:
        while True:
            remaining = self._time_until_next_candle_close()
            message = colorize(
                f"[TIMER] Next candle close in {remaining} ",
                YELLOW,
                bold=True,
            )
            sys.stdout.write("\r" + message)
            sys.stdout.flush()
            time.sleep(1)

    def _time_until_next_candle_close(self) -> str:
        now = datetime.now()
        bucket_start = now.replace(second=0, microsecond=0)
        minute_mod = bucket_start.minute % self.settings.candle_interval_minutes
        minutes_to_add = self.settings.candle_interval_minutes - minute_mod
        if minute_mod == 0 and now.second == 0:
            minutes_to_add = self.settings.candle_interval_minutes
        next_close = bucket_start + timedelta(minutes=minutes_to_add)
        remaining = max(int((next_close - now).total_seconds()), 0)
        minutes, seconds = divmod(remaining, 60)
        return f"{minutes:02d}:{seconds:02d}"

    def _volume_increment(
        self,
        symbol: str,
        current_volume: int,
        fallback_quantity: int,
    ) -> int:
        previous_volume = self._last_volumes.get(symbol)
        self._last_volumes[symbol] = max(current_volume, 0)
        if previous_volume is None:
            return max(fallback_quantity, 0)
        delta = current_volume - previous_volume
        if delta > 0:
            return delta
        return max(fallback_quantity, 0)

    @staticmethod
    def _default_on_candle(candle: Candle) -> None:
        logger.info("Closed candle: %s %s close=%.2f", candle.symbol, candle.end.isoformat(), candle.close)

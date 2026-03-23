from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from kiteconnect import KiteConnect

from execution.option_selector import get_current_weekly_expiry, round_to_nearest_strike


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PremiumQuote:
    trading_symbol: str
    last_price: float
    exchange: str = "NFO"


class OptionPremiumService:
    def __init__(self, kite: KiteConnect) -> None:
        self.kite = kite
        self._nfo_instruments: list[dict] | None = None

    def get_premium_quote(
        self,
        symbol: str,
        spot_price: float,
        signal: str,
        reference_date: date | None = None,
    ) -> PremiumQuote | None:
        normalized_symbol = symbol.upper()
        if normalized_symbol not in {"NIFTY", "BANKNIFTY"}:
            return None

        instrument_type = "CE" if signal.upper() == "CALL" else "PE"
        strike = round_to_nearest_strike(normalized_symbol, spot_price)
        expiry = get_current_weekly_expiry(reference_date)

        instrument = self._resolve_option_instrument(
            symbol=normalized_symbol,
            strike=strike,
            expiry=expiry,
            instrument_type=instrument_type,
        )
        if instrument is None:
            return None

        exchange_symbol = f"NFO:{instrument['tradingsymbol']}"
        ltp_response = self.kite.ltp(exchange_symbol)
        quote = ltp_response.get(exchange_symbol)
        if not quote:
            return None

        return PremiumQuote(
            trading_symbol=instrument["tradingsymbol"],
            last_price=float(quote["last_price"]),
        )

    def _resolve_option_instrument(
        self,
        symbol: str,
        strike: int,
        expiry: date,
        instrument_type: str,
    ) -> dict | None:
        if self._nfo_instruments is None:
            self._nfo_instruments = self.kite.instruments(exchange="NFO")

        return next(
            (
                row
                for row in self._nfo_instruments
                if row.get("name") == symbol
                and row.get("segment") == "NFO-OPT"
                and int(float(row.get("strike") or 0)) == strike
                and row.get("instrument_type") == instrument_type
                and row.get("expiry") == expiry
            ),
            None,
        )

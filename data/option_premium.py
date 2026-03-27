from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from kiteconnect import KiteConnect


logger = logging.getLogger(__name__)
OPTION_EXCHANGES = ("NFO", "BFO")
OPTION_SEGMENTS = {"NFO": "NFO-OPT", "BFO": "BFO-OPT"}


@dataclass(frozen=True)
class PremiumQuote:
    trading_symbol: str
    last_price: float
    exchange: str = "NFO"
    strike: int | None = None
    option_type: str | None = None


class OptionPremiumService:
    def __init__(self, kite: KiteConnect, market_type: str = "EQUITY") -> None:
        self.kite = kite
        self.market_type = market_type
        self._instrument_cache: dict[str, list[dict]] = {}

    def get_contract_quote(self, trading_symbol: str, exchange: str) -> PremiumQuote | None:
        exchange_symbol = f"{exchange}:{trading_symbol}"
        ltp_response = self.kite.ltp(exchange_symbol)
        quote = ltp_response.get(exchange_symbol)
        if not quote:
            return None
        return PremiumQuote(
            trading_symbol=trading_symbol,
            last_price=float(quote["last_price"]),
            exchange=exchange,
        )

    def get_premium_quote(
        self,
        symbol: str,
        spot_price: float,
        signal: str,
        reference_date: date | None = None,
    ) -> PremiumQuote | None:
        if self.market_type != "EQUITY":
            return None

        normalized_symbol = symbol.strip().upper()
        instrument_type = "CE" if signal.upper() == "CALL" else "PE"
        option_contract = self._select_option_contract(
            symbol=normalized_symbol,
            spot_price=spot_price,
            instrument_type=instrument_type,
            reference_date=reference_date or date.today(),
        )
        if option_contract is None:
            return None

        exchange_symbol = f"{option_contract['exchange']}:{option_contract['tradingsymbol']}"
        ltp_response = self.kite.ltp(exchange_symbol)
        quote = ltp_response.get(exchange_symbol)
        if not quote:
            return None

        normalized_strike = _normalized_option_strike(option_contract)
        return PremiumQuote(
            trading_symbol=str(option_contract["tradingsymbol"]),
            last_price=float(quote["last_price"]),
            exchange=str(option_contract["exchange"]),
            strike=int(normalized_strike) if normalized_strike is not None else None,
            option_type=instrument_type,
        )

    def _select_option_contract(
        self,
        symbol: str,
        spot_price: float,
        instrument_type: str,
        reference_date: date,
    ) -> dict | None:
        candidates: list[dict] = []
        for exchange in OPTION_EXCHANGES:
            instruments = self._load_instruments(exchange)
            segment = OPTION_SEGMENTS[exchange]
            candidates.extend(
                row
                for row in instruments
                if str(row.get("segment") or "").upper() == segment
                and str(row.get("instrument_type") or "").upper() == instrument_type
                and _option_name_matches(row, symbol)
                and row.get("expiry") is not None
                and float(row.get("strike") or 0) > 0
            )

        if not candidates:
            return None

        valid_expiries = sorted({row["expiry"] for row in candidates if row["expiry"] >= reference_date})
        if not valid_expiries:
            valid_expiries = sorted({row["expiry"] for row in candidates})
        if not valid_expiries:
            return None

        nearest_expiry = valid_expiries[0]
        expiry_candidates = [row for row in candidates if row.get("expiry") == nearest_expiry]
        expiry_candidates.sort(
            key=lambda row: (
                abs((_normalized_option_strike(row) or spot_price) - spot_price),
                0 if str(row.get("exchange") or "").upper() == "NFO" else 1,
                str(row.get("tradingsymbol") or ""),
            )
        )
        return expiry_candidates[0] if expiry_candidates else None

    def _load_instruments(self, exchange: str) -> list[dict]:
        instruments = self._instrument_cache.get(exchange)
        if instruments is None:
            instruments = self.kite.instruments(exchange=exchange)
            self._instrument_cache[exchange] = instruments
        return instruments


def _option_name_matches(row: dict, symbol: str) -> bool:
    tradingsymbol = str(row.get("tradingsymbol") or "").upper()
    name = str(row.get("name") or "").upper()
    return name == symbol or tradingsymbol.startswith(symbol)


def _normalized_option_strike(row: dict) -> float | None:
    raw_strike = row.get("strike")
    if raw_strike is not None:
        try:
            strike_value = float(raw_strike)
            if strike_value > 0:
                if strike_value >= 100000:
                    return strike_value / 100
                return strike_value
        except (TypeError, ValueError):
            pass

    tradingsymbol = str(row.get("tradingsymbol") or "").upper()
    option_type = str(row.get("instrument_type") or "").upper()
    suffix = option_type if option_type in {"CE", "PE"} else ""
    if suffix and tradingsymbol.endswith(suffix):
        tradingsymbol = tradingsymbol[: -len(suffix)]

    digits: list[str] = []
    for char in reversed(tradingsymbol):
        if char.isdigit():
            digits.append(char)
        elif digits:
            break
    if not digits:
        return None

    return float("".join(reversed(digits)))

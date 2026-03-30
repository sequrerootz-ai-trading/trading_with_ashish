from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime

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
    expiry: date | None = None
    volume: float | None = None
    oi: float | None = None


class OptionPremiumService:
    def __init__(self, kite: KiteConnect, market_type: str = "EQUITY") -> None:
        self.kite = kite
        self.market_type = market_type
        self._instrument_cache: dict[str, list[dict]] = {}
        self._instrument_cache_date: dict[str, date] = {}

    def get_contract_quote(self, trading_symbol: str, exchange: str) -> PremiumQuote | None:
        exchange_symbol = f"{exchange}:{trading_symbol}"
        quote = self._fetch_quote_snapshot(exchange_symbol)
        if not quote:
            logger.warning("No LTP quote found for %s", exchange_symbol)
            return None
        logger.info(
            "[OPTION_DATA] %s | LTP=%.2f | Volume=%s | OI=%s",
            exchange_symbol,
            float(quote["last_price"]),
            _fmt_optional_number(quote.get("volume")),
            _fmt_optional_number(quote.get("oi")),
        )
        return PremiumQuote(
            trading_symbol=trading_symbol,
            last_price=float(quote["last_price"]),
            exchange=exchange,
            volume=_coerce_optional_float(quote.get("volume")),
            oi=_coerce_optional_float(quote.get("oi")),
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
        requested_date = reference_date or date.today()
        instrument_type = "CE" if signal.upper() == "CALL" else "PE"
        option_contract = self._select_option_contract(
            symbol=normalized_symbol,
            spot_price=spot_price,
            instrument_type=instrument_type,
            reference_date=requested_date,
        )
        if option_contract is None:
            logger.warning(
                "No option contract found | symbol=%s spot=%.2f side=%s date=%s",
                normalized_symbol,
                spot_price,
                instrument_type,
                requested_date,
            )
            return None

        exchange_symbol = f"{option_contract['exchange']}:{option_contract['tradingsymbol']}"
        quote = self._fetch_quote_snapshot(exchange_symbol)
        if not quote:
            logger.warning("No premium quote returned for %s", exchange_symbol)
            return None

        normalized_strike = _normalized_option_strike(option_contract)
        expiry_value = option_contract.get("expiry")
        logger.info(
            "[OPTION_DATA] %s %s %s | LTP=%.2f | Volume=%s | OI=%s",
            normalized_symbol,
            int(normalized_strike) if normalized_strike is not None and float(normalized_strike).is_integer() else normalized_strike,
            instrument_type,
            float(quote["last_price"]),
            _fmt_optional_number(quote.get("volume")),
            _fmt_optional_number(quote.get("oi")),
        )
        return PremiumQuote(
            trading_symbol=str(option_contract["tradingsymbol"]),
            last_price=float(quote["last_price"]),
            exchange=str(option_contract["exchange"]),
            strike=int(normalized_strike) if normalized_strike is not None else None,
            option_type=instrument_type,
            expiry=expiry_value if isinstance(expiry_value, date) else None,
            volume=_coerce_optional_float(quote.get("volume")),
            oi=_coerce_optional_float(quote.get("oi")),
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
            instruments = self._load_instruments(exchange, reference_date)
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

        preferred_expiry = self._select_preferred_expiry(valid_expiries, reference_date)
        expiry_candidates = [row for row in candidates if row.get("expiry") == preferred_expiry]
        if not expiry_candidates:
            expiry_candidates = [row for row in candidates if row.get("expiry") == valid_expiries[0]]

        target_strikes = _preferred_strikes_for_spot(spot_price, instrument_type, expiry_candidates)
        available_strikes = sorted({_normalized_option_strike(row) for row in expiry_candidates if _normalized_option_strike(row) is not None})
        atm_strike = min(available_strikes, key=lambda strike: abs(strike - spot_price)) if available_strikes else None
        expiry_candidates.sort(
            key=lambda row: (
                0 if (_normalized_option_strike(row) in target_strikes) else 1,
                abs((_normalized_option_strike(row) or spot_price) - spot_price),
                0 if str(row.get("exchange") or "").upper() == "NFO" else 1,
                str(row.get("tradingsymbol") or ""),
            )
        )
        selected_contract = expiry_candidates[0] if expiry_candidates else None
        if selected_contract is not None:
            selected_strike = _normalized_option_strike(selected_contract)
            logger.info(
                "[OPTION_CHAIN] %s | Expiry=%s | ATM=%s | Selected Strike=%s | Type=%s",
                symbol,
                selected_contract.get("expiry"),
                _fmt_optional_number(atm_strike),
                _fmt_optional_number(selected_strike),
                instrument_type,
            )
        return selected_contract

    def _select_preferred_expiry(self, expiries: list[date], reference_date: date) -> date:
        if not expiries:
            raise ValueError("expiries cannot be empty")
        if reference_date == expiries[0] and datetime.now().hour >= 13 and len(expiries) > 1:
            return expiries[1]
        return expiries[0]

    # IMPROVED: refresh instrument dumps daily so expiry/contract resolution stays current.
    def _load_instruments(self, exchange: str, reference_date: date) -> list[dict]:
        instruments = self._instrument_cache.get(exchange)
        cache_date = self._instrument_cache_date.get(exchange)
        if instruments is None or cache_date != reference_date:
            instruments = self.kite.instruments(exchange=exchange)
            self._instrument_cache[exchange] = instruments
            self._instrument_cache_date[exchange] = reference_date
        return instruments


    def _fetch_quote_snapshot(self, exchange_symbol: str) -> dict | None:
        try:
            quote_response = self.kite.quote(exchange_symbol)
            quote = quote_response.get(exchange_symbol)
            if quote:
                return quote
        except Exception as exc:
            logger.debug("Full quote lookup failed for %s: %s", exchange_symbol, exc)

        ltp_response = self.kite.ltp(exchange_symbol)
        return ltp_response.get(exchange_symbol)


def _preferred_strikes_for_spot(spot_price: float, instrument_type: str, contracts: list[dict]) -> set[float]:
    strikes = sorted({_normalized_option_strike(row) for row in contracts if _normalized_option_strike(row) is not None})
    if not strikes:
        return set()
    atm = min(strikes, key=lambda strike: abs(strike - spot_price))
    lower = max([strike for strike in strikes if strike <= atm], default=atm)
    upper = min([strike for strike in strikes if strike >= atm], default=atm)
    if instrument_type == "CE":
        return {atm, lower}
    return {atm, upper}


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
            logger.debug("Unable to normalize strike from raw strike=%s", raw_strike)

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


def _coerce_optional_float(value) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_optional_number(value) -> str:
    numeric_value = _coerce_optional_float(value)
    if numeric_value is None:
        return "NA"
    if float(numeric_value).is_integer():
        return str(int(numeric_value))
    return f"{numeric_value:.2f}"

from __future__ import annotations

import logging
from datetime import date, datetime

from kiteconnect import KiteConnect


logger = logging.getLogger(__name__)
MCX_OPTION_SEGMENT = "MCX-OPT"
MCX_EXCHANGE = "MCX"
CACHE_TTL_SECONDS = 30
STRIKE_STEP = 100


class McxOptionChainService:
    def __init__(self, kite: KiteConnect) -> None:
        self.kite = kite
        self._instrument_cache: list[dict] | None = None
        self._instrument_cache_date: date | None = None
        self._chain_cache: dict[tuple[str, int, str], tuple[datetime, list[dict]]] = {}

    def get_option_chain(
        self,
        symbol: str,
        spot_price: float,
        reference_date: date | None = None,
    ) -> list[dict]:
        normalized_symbol = symbol.strip().upper()
        now = datetime.now()
        requested_date = reference_date or date.today()
        atm_strike = int(round(float(spot_price) / STRIKE_STEP) * STRIKE_STEP)
        cache_key = (normalized_symbol, atm_strike, requested_date.isoformat())
        cached = self._chain_cache.get(cache_key)
        if cached is not None:
            cached_at, cached_chain = cached
            if (now - cached_at).total_seconds() <= CACHE_TTL_SECONDS:
                return list(cached_chain)

        contracts = self._select_contracts(normalized_symbol, spot_price, requested_date)
        if not contracts:
            logger.info("[OPTION_CHAIN] %s | Data not available", normalized_symbol)
            return []

        exchange_symbols = [f"{row['exchange']}:{row['tradingsymbol']}" for row in contracts]
        quotes = self._fetch_quotes(exchange_symbols)

        option_chain: list[dict] = []
        for contract in contracts:
            exchange_symbol = f"{contract['exchange']}:{contract['tradingsymbol']}"
            quote = quotes.get(exchange_symbol)
            if not quote:
                continue
            ltp = float(quote.get("last_price") or 0.0)
            if ltp <= 0:
                continue
            strike = _normalized_option_strike(contract)
            if strike is None:
                continue
            option_chain.append(
                {
                    "strike": int(round(strike)),
                    "option_type": str(contract.get("instrument_type") or "").upper(),
                    "ltp": round(ltp, 2),
                    "volume": float(quote.get("volume") or 0.0),
                    "oi": float(quote.get("oi") or 0.0),
                    "tradingsymbol": str(contract.get("tradingsymbol") or ""),
                    "exchange": str(contract.get("exchange") or MCX_EXCHANGE),
                    "expiry": contract.get("expiry").isoformat() if contract.get("expiry") is not None else None,
                }
            )

        if option_chain:
            selected_expiry = option_chain[0].get("expiry")
            logger.info("[OPTION_CHAIN] %s | Expiry=%s | ATM=%s | Contracts=%s", normalized_symbol, selected_expiry or "NA", atm_strike, len(option_chain))
        self._chain_cache[cache_key] = (now, option_chain)
        return list(option_chain)

    def _select_contracts(self, symbol: str, spot_price: float, reference_date: date) -> list[dict]:
        instruments = self._load_instruments(reference_date)
        candidates = [
            row
            for row in instruments
            if str(row.get("segment") or "").upper() == MCX_OPTION_SEGMENT
            and _option_name_matches(row, symbol)
            and row.get("expiry") is not None
            and str(row.get("instrument_type") or "").upper() in {"CE", "PE"}
            and float(row.get("strike") or 0) > 0
        ]
        if not candidates:
            return []

        valid_expiries = sorted({row["expiry"] for row in candidates if row["expiry"] >= reference_date})
        if not valid_expiries:
            valid_expiries = sorted({row["expiry"] for row in candidates})
        if not valid_expiries:
            return []

        expiry = valid_expiries[0]
        expiry_candidates = [row for row in candidates if row.get("expiry") == expiry]
        atm_strike = int(round(float(spot_price) / STRIKE_STEP) * STRIKE_STEP)
        preferred_strikes = {atm_strike - STRIKE_STEP, atm_strike, atm_strike + STRIKE_STEP}
        selected = [
            row
            for row in expiry_candidates
            if _normalized_option_strike(row) is not None and int(round(_normalized_option_strike(row) or 0.0)) in preferred_strikes
        ]
        if selected:
            return selected
        return expiry_candidates

    def _load_instruments(self, reference_date: date) -> list[dict]:
        if self._instrument_cache is None or self._instrument_cache_date != reference_date:
            self._instrument_cache = self.kite.instruments(exchange=MCX_EXCHANGE)
            self._instrument_cache_date = reference_date
        return self._instrument_cache

    def _fetch_quotes(self, exchange_symbols: list[str]) -> dict[str, dict]:
        if not exchange_symbols:
            return {}
        try:
            return self.kite.quote(*exchange_symbols)
        except Exception as exc:
            logger.warning("MCX option quote fetch failed: %s", exc)
            return {}


def _option_name_matches(row: dict, symbol: str) -> bool:
    tradingsymbol = str(row.get("tradingsymbol") or "").upper()
    name = str(row.get("name") or "").upper()
    return name == symbol or tradingsymbol.startswith(symbol)


def _normalized_option_strike(row: dict) -> float | None:
    raw_strike = row.get("strike")
    try:
        strike_value = float(raw_strike)
        if strike_value > 0:
            if strike_value >= 100000:
                return strike_value / 100
            return strike_value
    except (TypeError, ValueError):
        return None
    return None

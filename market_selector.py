from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from kiteconnect import KiteConnect

from config.settings import InstrumentConfig, Settings


@dataclass(frozen=True)
class MarketProfile:
    market_type: str
    exchange: str
    supports_options: bool
    uses_sentiment: bool
    signal_mode: str


@dataclass(frozen=True)
class InstrumentSelection:
    instrument_token: int
    exchange: str
    tradingsymbol: str


EQUITY_ALIAS_MAP = {
    "NIFTY": ["NIFTY", "NIFTY 50"],
    "BANKNIFTY": ["BANKNIFTY", "NIFTY BANK", "BANK NIFTY"],
    "SENSEX": ["SENSEX"],
    "BANKEX": ["BANKEX"],
}


def get_market_profile(settings: Settings) -> MarketProfile:
    instrument = settings.instruments[0]
    if settings.market_type == "EQUITY":
        return MarketProfile(
            market_type="EQUITY",
            exchange=instrument.exchange,
            supports_options=True,
            uses_sentiment=False,
            signal_mode="OPTIONS",
        )

    return MarketProfile(
        market_type="MCX",
        exchange=instrument.exchange,
        supports_options=False,
        uses_sentiment=False,
        signal_mode="DIRECTIONAL",
    )


def get_instrument_config(symbol: str, settings: Settings) -> InstrumentConfig:
    normalized_symbol = symbol.strip().upper()
    for instrument in settings.instruments:
        if instrument.label == normalized_symbol:
            return instrument
    raise ValueError(f"Instrument config not found for symbol={symbol}")


def get_instrument_token(symbol: str, market_type: str, kite: KiteConnect, settings: Settings) -> int:
    return resolve_instrument_selection(symbol, market_type, kite, settings).instrument_token


def resolve_instrument_selection(
    symbol: str,
    market_type: str,
    kite: KiteConnect,
    settings: Settings,
) -> InstrumentSelection:
    instrument = get_instrument_config(symbol, settings)
    if instrument.market_type != market_type:
        raise ValueError(
            f"Symbol {symbol} belongs to {instrument.market_type}, not requested market type {market_type}"
        )

    if market_type == "MCX":
        rows = kite.instruments(exchange=instrument.exchange)
        match = _select_mcx_future(symbol, rows)
    else:
        match = _select_equity_instrument(instrument, kite)

    if match is None:
        raise ValueError(
            f"Instrument token not found for {market_type}:{symbol} -> {instrument.exchange}:{instrument.tradingsymbol}"
        )

    return InstrumentSelection(
        instrument_token=int(match["instrument_token"]),
        exchange=str(match.get("exchange") or instrument.exchange),
        tradingsymbol=str(match.get("tradingsymbol") or instrument.tradingsymbol),
    )


def _select_mcx_future(symbol: str, rows: list[dict]) -> dict | None:
    today = date.today()
    candidates = [
        row
        for row in rows
        if str(row.get("name", "")).upper() == symbol.upper()
        and str(row.get("segment", "")).upper().startswith("MCX-FUT")
        and row.get("expiry") is not None
    ]
    if not candidates:
        return None

    candidates.sort(key=lambda row: (row["expiry"] < today, row["expiry"]))
    return candidates[0]


def _select_equity_instrument(instrument: InstrumentConfig, kite: KiteConnect) -> dict | None:
    exchanges = [instrument.exchange] if instrument.exchange not in {"", "AUTO"} else ["NSE", "BSE"]
    aliases = _equity_aliases(instrument)

    best_match: dict | None = None
    best_score = -1
    for exchange in exchanges:
        rows = kite.instruments(exchange=exchange)
        for row in rows:
            score = _equity_match_score(row, instrument, aliases, exchange)
            if score > best_score:
                best_match = row
                best_score = score

    return best_match if best_score >= 0 else None


def _equity_aliases(instrument: InstrumentConfig) -> list[str]:
    label = instrument.label.strip().upper()
    tradingsymbol = instrument.tradingsymbol.strip().upper()
    aliases = EQUITY_ALIAS_MAP.get(label, [label, tradingsymbol])
    combined: list[str] = []
    for value in [label, tradingsymbol, *aliases]:
        normalized = value.strip().upper()
        if normalized and normalized not in combined:
            combined.append(normalized)
    return combined


def _equity_match_score(row: dict, instrument: InstrumentConfig, aliases: list[str], exchange: str) -> int:
    tradingsymbol = str(row.get("tradingsymbol") or "").upper()
    name = str(row.get("name") or "").upper()
    segment = str(row.get("segment") or "").upper()
    instrument_type = str(row.get("instrument_type") or "").upper()

    score = -1
    if tradingsymbol in aliases:
        score = max(score, 100)
    if name in aliases:
        score = max(score, 90)
    if instrument.label == tradingsymbol:
        score = max(score, 95)
    if score < 0:
        return -1

    if instrument.segment == "INDEX" and (segment.endswith("INDICES") or instrument_type == "INDEX"):
        score += 20
    elif instrument.segment in {"AUTO", "CASH"} and instrument_type == "EQ":
        score += 15
    elif instrument.segment == "EQUITY" and instrument_type == "EQ":
        score += 20

    if exchange == "NSE":
        score += 5
    return score

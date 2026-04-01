from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta


STEP_SIZE_BY_INDEX = {
    "NIFTY": 50,
    "BANKNIFTY": 100,
    "SENSEX": 100,
}

OPTION_TYPE_BY_SIGNAL = {
    "CALL": "CE",
    "PUT": "PE",
}

MONTH_CODES = {
    1: "JAN",
    2: "FEB",
    3: "MAR",
    4: "APR",
    5: "MAY",
    6: "JUN",
    7: "JUL",
    8: "AUG",
    9: "SEP",
    10: "OCT",
    11: "NOV",
    12: "DEC",
}


@dataclass(frozen=True)
class OptionContract:
    index_name: str
    strike: int
    expiry: date
    option_type: str
    trading_symbol: str


def round_to_nearest_strike(index_name: str, index_price: float) -> int:
    normalized_index = index_name.upper()
    if normalized_index not in STEP_SIZE_BY_INDEX:
        raise ValueError(f"Unsupported index: {index_name}")

    step_size = STEP_SIZE_BY_INDEX[normalized_index]
    return int(round(index_price / step_size) * step_size)


def get_current_weekly_expiry(index_name: str, reference_date: date | None = None) -> date:
    current_date = reference_date or date.today()
    normalized_index = index_name.upper()
    if normalized_index == "NIFTY":
        return _next_weekday(current_date, 1)
    if normalized_index == "SENSEX":
        return _next_weekday(current_date, 3)
    if normalized_index == "BANKNIFTY":
        return _last_weekday_of_month(current_date, 1)
    raise ValueError(f"Unsupported index: {index_name}")


def _next_weekday(current_date: date, target_weekday: int) -> date:
    days_ahead = (target_weekday - current_date.weekday()) % 7
    return current_date + timedelta(days=days_ahead)


def _last_weekday_of_month(current_date: date, target_weekday: int) -> date:
    last_day = calendar.monthrange(current_date.year, current_date.month)[1]
    candidate = date(current_date.year, current_date.month, last_day)
    while candidate.weekday() != target_weekday:
        candidate -= timedelta(days=1)
    return candidate


def build_option_trading_symbol(
    index_name: str,
    index_price: float,
    signal: str,
    reference_date: date | None = None,
) -> str:
    normalized_index = index_name.upper()
    normalized_signal = signal.upper()

    if normalized_signal not in OPTION_TYPE_BY_SIGNAL:
        raise ValueError(f"Unsupported signal: {signal}")

    strike = round_to_nearest_strike(normalized_index, index_price)
    expiry = get_current_weekly_expiry(normalized_index, reference_date=reference_date)
    option_type = OPTION_TYPE_BY_SIGNAL[normalized_signal]
    expiry_code = f"{expiry:%y}{MONTH_CODES[expiry.month]}{expiry.day:02d}"

    return f"{normalized_index}{expiry_code}{strike}{option_type}"


def select_option_contract(
    index_name: str,
    index_price: float,
    signal: str,
    reference_date: date | None = None,
) -> OptionContract:
    normalized_index = index_name.upper()
    normalized_signal = signal.upper()

    if normalized_signal not in OPTION_TYPE_BY_SIGNAL:
        raise ValueError(f"Unsupported signal: {signal}")

    expiry = get_current_weekly_expiry(normalized_index, reference_date=reference_date)
    strike = round_to_nearest_strike(normalized_index, index_price)
    option_type = OPTION_TYPE_BY_SIGNAL[normalized_signal]
    trading_symbol = build_option_trading_symbol(
        index_name=normalized_index,
        index_price=index_price,
        signal=normalized_signal,
        reference_date=reference_date,
    )

    return OptionContract(
        index_name=normalized_index,
        strike=strike,
        expiry=expiry,
        option_type=option_type,
        trading_symbol=trading_symbol,
    )

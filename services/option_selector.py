from __future__ import annotations

from execution.option_selector import get_current_weekly_expiry, round_to_nearest_strike
from strategy.signal_types import OptionSuggestion


STRIKE_OFFSETS = {
    "BUY_CE": 0,
    "BUY_PE": 0,
}


def select_nifty_option(spot_price: float, signal: str) -> OptionSuggestion:
    normalized_signal = signal.strip().upper()
    option_type = "CE" if normalized_signal == "BUY_CE" else "PE"
    base_strike = round_to_nearest_strike("NIFTY", spot_price)
    strike = base_strike + STRIKE_OFFSETS.get(normalized_signal, 0)
    expiry = get_current_weekly_expiry("NIFTY").isoformat()
    return OptionSuggestion(
        strike=strike,
        option_type=option_type,
        label=f"{strike} {option_type}",
        expiry=expiry,
    )

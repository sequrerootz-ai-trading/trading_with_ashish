from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from strategy.signal_types import GeneratedSignal, IndicatorDetails, OptionSuggestion, SignalDetails


logger = logging.getLogger(__name__)
STRIKE_STEP = 100


def select_mcx_option(symbol: str, signal: str, spot_price: float, option_chain: list[dict[str, Any]] | None):
    if not option_chain:
        return None

    normalized_signal = signal.strip().upper()
    if normalized_signal not in {"BUY", "SELL"}:
        return None

    option_type = "CE" if normalized_signal == "BUY" else "PE"
    atm_strike = int(round(float(spot_price) / STRIKE_STEP) * STRIKE_STEP)
    preferred_strikes = [atm_strike] if normalized_signal == "BUY" else [atm_strike]

    normalized_options: list[dict[str, float | str]] = []
    for item in option_chain:
        try:
            strike = int(round(float(item.get("strike") or 0)))
            ltp = float(item.get("ltp") or item.get("premium") or item.get("last_price") or 0.0)
        except (TypeError, ValueError):
            continue
        item_option_type = str(item.get("type") or item.get("option_type") or "").strip().upper()
        if strike <= 0 or ltp <= 0 or item_option_type != option_type:
            continue
        normalized_options.append(
            {
                "strike": strike,
                "type": item_option_type,
                "ltp": round(ltp, 2),
                "exchange": str(item.get("exchange") or "MCX"),
                "tradingsymbol": str(item.get("tradingsymbol") or f"{symbol} {strike} {item_option_type}"),
                "expiry": item.get("expiry"),
            }
        )

    if not normalized_options:
        return None

    for preferred_strike in preferred_strikes:
        selected = next((option for option in normalized_options if option["strike"] == preferred_strike), None)
        if selected is not None:
            return selected

    return min(normalized_options, key=lambda option: abs(int(option["strike"]) - atm_strike))


def enrich_mcx_signal_with_option(
    symbol: str,
    generated_signal: GeneratedSignal,
    spot_price: float,
    option_chain: list[dict[str, Any]] | None,
) -> GeneratedSignal:
    if generated_signal.signal not in {"BUY", "SELL"}:
        return generated_signal

    selected_option = select_mcx_option(symbol, generated_signal.signal, spot_price, option_chain)
    if selected_option is None:
        logger.info("[OPTION] %s | Data not available", symbol)
        return generated_signal

    entry_price = float(selected_option["ltp"])
    target = round(entry_price * 1.20, 2)
    stop_loss = round(entry_price * 0.85, 2)
    strike = int(selected_option["strike"])
    option_type = str(selected_option["type"])
    exchange = str(selected_option.get("exchange") or "MCX")
    trading_symbol = str(selected_option.get("tradingsymbol") or f"{symbol} {strike} {option_type}")
    expiry = str(selected_option.get("expiry")) if selected_option.get("expiry") else None

    option_suggestion = OptionSuggestion(
        strike=strike,
        option_type=option_type,
        label=f"{strike} {option_type}",
        premium_ltp=entry_price,
        trading_symbol=trading_symbol,
        exchange=exchange,
        expiry=expiry,
        entry_low=entry_price,
        entry_high=entry_price,
        stop_loss=stop_loss,
        target=target,
    )
    details = SignalDetails(
        action_label="Buy" if generated_signal.signal == "BUY" else "Sell",
        confidence_pct=int(round(generated_signal.confidence * 100)),
        confidence_label="High" if generated_signal.confidence >= 0.8 else "Moderate" if generated_signal.confidence >= 0.6 else "Low",
        risk_label="Normal Entry",
        indicator_details=IndicatorDetails(),
        option_suggestion=option_suggestion,
        summary=generated_signal.reason,
    )

    logger.info(
        "[OPTION] %s | %s %s | Expiry=%s | Entry=%.2f | Target=%.2f | SL=%.2f",
        symbol,
        strike,
        option_type,
        expiry or "NA",
        entry_price,
        target,
        stop_loss,
    )

    return replace(
        generated_signal,
        details=details,
        entry_price=entry_price,
        target=target,
        stop_loss=stop_loss,
        context={
            **getattr(generated_signal, "context", {}),
            "option_strike": strike,
            "option_type": option_type,
            "option_entry_price": entry_price,
            "option_target": target,
            "option_stop_loss": stop_loss,
        },
    )

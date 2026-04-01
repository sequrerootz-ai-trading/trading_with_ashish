from __future__ import annotations

import logging
from dataclasses import replace

from config import get_mode
from data.option_premium import PremiumQuote
from engine.signal_engine import evaluate_nifty_price_action
from services.option_selector import select_nifty_option
from strategy.signal_types import GeneratedSignal, IndicatorDetails, OptionSuggestion, SignalContext, SignalDetails
from utils.calculations import premium_trade_levels


logger = logging.getLogger(__name__)


def generate_nifty_options_signal(data: SignalContext) -> GeneratedSignal:
    if get_mode().upper() == "PAPER":
        logger.info("[MODE] PAPER TRADE")

    if data.last_candle is None or len(data.candles) < 21:
        return GeneratedSignal(
            symbol=data.symbol,
            timestamp="",
            signal="NO_TRADE",
            reason="insufficient_closed_candles",
            confidence=0.0,
        )

    analysis = evaluate_nifty_price_action(data)
    current_candle = data.last_candle
    trend = str(analysis["trend"])
    bullish_break = bool(analysis["bullish_break"])
    bearish_break = bool(analysis["bearish_break"])
    close_position = float(analysis["close_position"])
    bullish_score = int(analysis["bullish_score"])
    bearish_score = int(analysis["bearish_score"])

    signal = "NO_TRADE"
    confidence = 0.0
    reason: list[str] = []

    if trend == "bullish" and bullish_break and close_position >= (0.48 if data.timeframe_minutes <= 3 else 0.55):
        signal = "BUY_CE"
        confidence = min(0.55 + (bullish_score * 0.08), 0.82)
        reason.extend(["ema_trend_up", "soft_breakout", f"score={bullish_score}"])
    elif trend == "bearish" and bearish_break and close_position <= (0.52 if data.timeframe_minutes <= 3 else 0.45):
        signal = "BUY_PE"
        confidence = min(0.55 + (bearish_score * 0.08), 0.82)
        reason.extend(["ema_trend_down", "soft_breakdown", f"score={bearish_score}"])
    else:
        reason.append("soft_filter_not_met")

    reason.extend(
        [
            f"ema9={_fmt(analysis['ema_9'])}",
            f"ema21={_fmt(analysis['ema_21'])}",
            f"rsi={_fmt(analysis['rsi'])}",
            f"trend={trend}",
            f"timeframe={data.timeframe_minutes}m",
            f"breakout={float(analysis['breakout_level']):.2f}",
            f"breakdown={float(analysis['breakdown_level']):.2f}",
            f"volume_ok={bool(analysis['volume_ok'])}",
            f"momentum_ok={bool(analysis['momentum_ok'])}",
            f"volatility_ok={bool(analysis['volatility_ok'])}",
            f"close_pos={close_position:.2f}",
        ]
    )

    indicator_details = IndicatorDetails(
        ema_9=float(analysis["ema_9"]) if analysis["ema_9"] is not None else None,
        ema_21=float(analysis["ema_21"]) if analysis["ema_21"] is not None else None,
        rsi=float(analysis["rsi"]) if analysis["rsi"] is not None else None,
        trend=trend,
        breakout_price=float(analysis["breakout_level"]),
        breakdown_price=float(analysis["breakdown_level"]),
        volume_ratio=float(analysis["volume_ratio"]) if analysis["volume_ratio"] is not None else None,
        market_condition=f"nifty_{trend}",
        rsi_state="normal",
    )

    option_suggestion = None
    if signal in {"BUY_CE", "BUY_PE"}:
        option_suggestion = select_nifty_option(float(current_candle.close), signal)

    details = SignalDetails(
        action_label="Buy CE" if signal == "BUY_CE" else "Buy PE" if signal == "BUY_PE" else "No trade",
        confidence_pct=int(round(confidence * 100)),
        confidence_label="High" if confidence >= 0.8 else "Moderate" if confidence >= 0.6 else "Low",
        risk_label="Normal Entry",
        indicator_details=indicator_details,
        option_suggestion=option_suggestion,
        summary=" ".join(reason),
    )

    logger.info(
        "[NIFTY_OPTION_SIGNAL] signal=%s | confidence=%.2f | reason=%s",
        signal,
        confidence,
        details.summary,
    )

    return GeneratedSignal(
        symbol=data.symbol,
        timestamp=current_candle.end.isoformat(),
        signal=signal,
        reason=details.summary,
        confidence=confidence,
        details=details,
        context={
            "model": "NIFTY_OPTIONS",
            "option_type": (option_suggestion.option_type if option_suggestion is not None else None),
            "atm_strike": (option_suggestion.strike if option_suggestion is not None else None),
            "expiry": (option_suggestion.expiry if option_suggestion is not None else None),
        },
    )


def enrich_nifty_signal_with_premium(signal: GeneratedSignal, premium: PremiumQuote | None) -> GeneratedSignal:
    if signal.signal not in {"BUY_CE", "BUY_PE"} or signal.details is None or signal.details.option_suggestion is None or premium is None:
        return signal

    trade_levels = premium_trade_levels(premium.last_price, target_pct=0.20, stop_loss_pct=0.15)
    option = replace(
        signal.details.option_suggestion,
        strike=premium.strike,
        label=f"{premium.strike} {premium.option_type}" if premium.strike is not None and premium.option_type is not None else signal.details.option_suggestion.label,
        premium_ltp=round(premium.last_price, 2),
        trading_symbol=premium.trading_symbol,
        exchange=premium.exchange,
        expiry=premium.expiry.isoformat() if premium.expiry is not None else signal.details.option_suggestion.expiry,
        entry_low=trade_levels["entry_price"],
        entry_high=trade_levels["entry_price"],
        stop_loss=trade_levels["stop_loss"],
        target=trade_levels["target"],
    )
    details = replace(signal.details, option_suggestion=option, summary=signal.reason)

    logger.info(
        "[OPTION] %s | %s %s | Expiry=%s | Entry=%.2f | Target=%.2f | SL=%.2f",
        signal.symbol,
        premium.strike if premium.strike is not None else "NA",
        premium.option_type or option.option_type,
        option.expiry or "NA",
        trade_levels["entry_price"],
        trade_levels["target"],
        trade_levels["stop_loss"],
    )

    return replace(
        signal,
        details=details,
        entry_price=trade_levels["entry_price"],
        target=trade_levels["target"],
        stop_loss=trade_levels["stop_loss"],
        context={
            **getattr(signal, "context", {}),
            "option_strike": premium.strike,
            "option_type": premium.option_type,
            "option_entry_price": trade_levels["entry_price"],
            "option_target": trade_levels["target"],
            "option_stop_loss": trade_levels["stop_loss"],
        },
    )


# Backward compatibility for older imports.
generate_nifty_hybrid_signal = generate_nifty_options_signal


def _fmt(value: float | None) -> str:
    if value is None:
        return "NA"
    return f"{value:.2f}"

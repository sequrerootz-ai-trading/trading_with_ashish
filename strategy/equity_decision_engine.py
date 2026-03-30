from __future__ import annotations

from dataclasses import replace

from data.option_premium import PremiumQuote
from strategy.signal_types import (
    GeneratedSignal,
    IndicatorDetails,
    OptionSuggestion,
    SignalDetails,
    SignalContext,
    ValidationItem,
)


BULLISH_RSI_RANGE = (55.0, 75.0)
BEARISH_RSI_RANGE = (25.0, 45.0)
OVERBOUGHT_RSI = 75.0
OVERSOLD_RSI = 25.0


def build_equity_decision(symbol: str, data: SignalContext) -> GeneratedSignal:
    if data.last_candle is None or len(data.candles) < 21:
        return GeneratedSignal(
            symbol=symbol,
            timestamp="",
            signal="NO_TRADE",
            reason="insufficient_closed_candles",
            confidence=0.0,
        )

    from strategy.indicators import calculate_indicators, detect_trend

    close_prices = [candle.close for candle in data.candles]
    indicators = calculate_indicators(close_prices)
    current_candle = data.last_candle
    previous_candle = data.candles[-2]

    if indicators.ema_9 is None or indicators.ema_21 is None or indicators.rsi is None:
        return GeneratedSignal(
            symbol=symbol,
            timestamp=current_candle.end.isoformat(),
            signal="NO_TRADE",
            reason="indicator_warmup_pending",
            confidence=0.0,
        )

    trend = detect_trend(indicators.ema_9, indicators.ema_21)
    market_condition = detect_market_condition(
        ema_9=indicators.ema_9,
        ema_21=indicators.ema_21,
        rsi=indicators.rsi,
        last_price=current_candle.close,
    )
    validation = validate_signal(
        ema_9=indicators.ema_9,
        ema_21=indicators.ema_21,
        rsi=indicators.rsi,
        current_close=current_candle.close,
        current_high=current_candle.high,
        current_low=current_candle.low,
        previous_high=previous_candle.high,
        previous_low=previous_candle.low,
    )
    volume_ratio = _volume_ratio(data.candles)
    confidence_pct = 0
    if validation["signal"] != "NO_TRADE":
        confidence_pct = calculate_confidence(
            ema_9=indicators.ema_9,
            ema_21=indicators.ema_21,
            rsi=indicators.rsi,
            current_close=current_candle.close,
            previous_high=previous_candle.high,
            previous_low=previous_candle.low,
            direction=str(validation["direction"]),
            is_risky=bool(validation["is_risky"]),
            volume_ratio=volume_ratio,
        )

    indicator_details = IndicatorDetails(
        ema_9=indicators.ema_9,
        ema_21=indicators.ema_21,
        rsi=indicators.rsi,
        trend=trend,
        trend_strength_pct=_trend_strength_pct(indicators.ema_9, indicators.ema_21, current_candle.close),
        breakout_price=previous_candle.high,
        breakdown_price=previous_candle.low,
        volume_ratio=volume_ratio,
        market_condition=market_condition,
        rsi_state=str(validation["rsi_state"]),
    )

    if validation["signal"] == "NO_TRADE":
        return GeneratedSignal(
            symbol=symbol,
            timestamp=current_candle.end.isoformat(),
            signal="NO_TRADE",
            reason=str(validation["reason"]),
            confidence=0.0,
            details=SignalDetails(
                action_label="No trade",
                confidence_pct=0,
                confidence_label="No edge",
                risk_label="Stand aside",
                indicator_details=indicator_details,
                validations=list(validation["validations"]),
                summary=str(validation["summary"]),
            ),
        )

    option_suggestion = select_option_strike(symbol, current_candle.close, str(validation["direction"]))
    premium_levels = calculate_premium_trade_levels(
        option_suggestion.premium_ltp if option_suggestion.premium_ltp is not None else current_candle.close,
        confidence_pct=confidence_pct,
    )
    option_suggestion = replace(
        option_suggestion,
        entry_low=premium_levels["entry_price"],
        entry_high=premium_levels["entry_price"],
        stop_loss=premium_levels["stop_loss"],
        target=premium_levels["target"],
    )
    confidence_label = _confidence_label(confidence_pct)
    risk_label = "Risky Entry" if bool(validation["is_risky"]) else "Normal Entry"
    details = SignalDetails(
        action_label=str(validation["action_label"]),
        confidence_pct=confidence_pct,
        confidence_label=confidence_label,
        risk_label=risk_label,
        indicator_details=indicator_details,
        validations=list(validation["validations"]),
        option_suggestion=option_suggestion,
        summary=str(validation["summary"]),
    )
    return GeneratedSignal(
        symbol=symbol,
        timestamp=current_candle.end.isoformat(),
        signal=str(validation["signal"]),
        reason=str(validation["reason"]),
        confidence=confidence_pct / 100.0,
        details=details,
        entry_price=premium_levels["entry_price"],
        target=premium_levels["target"],
        stop_loss=premium_levels["stop_loss"],
    )


def calculate_confidence(
    ema_9: float,
    ema_21: float,
    rsi: float,
    current_close: float,
    previous_high: float,
    previous_low: float,
    direction: str,
    is_risky: bool,
    volume_ratio: float | None = None,
) -> int:
    trend_strength = min(_trend_strength_pct(ema_9, ema_21, current_close) / 0.40, 1.0)
    if direction == "bullish":
        breakout_strength = max(current_close - previous_high, 0.0)
        breakout_reference = max(current_close * 0.003, 1.0)
        breakout_score = min(breakout_strength / breakout_reference, 1.0)
        rsi_score = _bullish_rsi_score(rsi)
    else:
        breakout_strength = max(previous_low - current_close, 0.0)
        breakout_reference = max(current_close * 0.003, 1.0)
        breakout_score = min(breakout_strength / breakout_reference, 1.0)
        rsi_score = _bearish_rsi_score(rsi)

    volume_score = 0.5 if volume_ratio is None else min(max((volume_ratio - 0.8) / 0.7, 0.0), 1.0)
    weighted_score = (trend_strength * 0.35) + (rsi_score * 0.25) + (breakout_score * 0.25) + (volume_score * 0.15)
    confidence = int(round(55 + (weighted_score * 35)))
    if is_risky:
        confidence -= 10
    return max(45, min(confidence, 92))


def validate_signal(
    ema_9: float,
    ema_21: float,
    rsi: float,
    current_close: float,
    current_high: float,
    current_low: float,
    previous_high: float,
    previous_low: float,
) -> dict[str, object]:
    bullish_breakout = current_high > previous_high and current_close >= previous_high * 0.999
    bearish_breakout = current_low < previous_low and current_close <= previous_low * 1.001
    bullish_trend = ema_9 > ema_21
    bearish_trend = ema_9 < ema_21
    overbought = rsi > OVERBOUGHT_RSI
    oversold = rsi < OVERSOLD_RSI
    bullish_rsi_healthy = BULLISH_RSI_RANGE[0] <= rsi <= BULLISH_RSI_RANGE[1]
    bearish_rsi_healthy = BEARISH_RSI_RANGE[0] <= rsi <= BEARISH_RSI_RANGE[1]

    bullish_checks = [
        ValidationItem("EMA Trend Confirmed", "pass" if bullish_trend else "fail", bullish_trend),
        ValidationItem("Breakout Confirmed", "pass" if bullish_breakout else "fail", bullish_breakout),
        ValidationItem(
            "RSI in healthy bullish zone",
            "pass" if bullish_rsi_healthy else ("warn" if overbought else "fail"),
            bullish_rsi_healthy,
        ),
    ]
    bearish_checks = [
        ValidationItem("EMA Trend Confirmed", "pass" if bearish_trend else "fail", bearish_trend),
        ValidationItem("Breakdown Confirmed", "pass" if bearish_breakout else "fail", bearish_breakout),
        ValidationItem(
            "RSI in healthy bearish zone",
            "pass" if bearish_rsi_healthy else ("warn" if oversold else "fail"),
            bearish_rsi_healthy,
        ),
    ]

    if bullish_trend and bullish_breakout and (bullish_rsi_healthy or overbought):
        return {
            "signal": "BUY_CE",
            "direction": "bullish",
            "action_label": "BUY CE (Bullish)",
            "reason": _build_reason(
                trend="bullish",
                breakout="price_breakout",
                rsi_state="overbought" if overbought else "healthy",
                ema_9=ema_9,
                ema_21=ema_21,
                rsi=rsi,
                previous_high=previous_high,
                previous_low=previous_low,
            ),
            "summary": "Bullish breakout aligned with EMA trend",
            "is_risky": overbought,
            "rsi_state": "overbought" if overbought else "healthy",
            "validations": bullish_checks,
        }

    if bearish_trend and bearish_breakout and (bearish_rsi_healthy or oversold):
        return {
            "signal": "BUY_PE",
            "direction": "bearish",
            "action_label": "BUY PE (Bearish)",
            "reason": _build_reason(
                trend="bearish",
                breakout="price_breakdown",
                rsi_state="oversold" if oversold else "healthy",
                ema_9=ema_9,
                ema_21=ema_21,
                rsi=rsi,
                previous_high=previous_high,
                previous_low=previous_low,
            ),
            "summary": "Bearish breakdown aligned with EMA trend",
            "is_risky": oversold,
            "rsi_state": "oversold" if oversold else "healthy",
            "validations": bearish_checks,
        }

    return {
        "signal": "NO_TRADE",
        "direction": "neutral",
        "action_label": "No trade",
        "reason": _build_no_trade_reason(
            bullish_trend=bullish_trend,
            bearish_trend=bearish_trend,
            bullish_breakout=bullish_breakout,
            bearish_breakout=bearish_breakout,
            rsi=rsi,
            ema_9=ema_9,
            ema_21=ema_21,
            previous_high=previous_high,
            previous_low=previous_low,
        ),
        "summary": "Technical conditions are not aligned for a clean entry",
        "is_risky": False,
        "rsi_state": "overbought" if overbought else ("oversold" if oversold else "normal"),
        "validations": bullish_checks if ema_9 >= ema_21 else bearish_checks,
    }


def detect_market_condition(ema_9: float, ema_21: float, rsi: float, last_price: float) -> str:
    strength = _trend_strength_pct(ema_9, ema_21, last_price)
    if ema_9 > ema_21:
        if rsi > OVERBOUGHT_RSI:
            return "bullish but extended"
        if strength >= 0.35:
            return "strong bullish trend"
        return "steady bullish trend"
    if ema_9 < ema_21:
        if rsi < OVERSOLD_RSI:
            return "bearish but extended"
        if strength >= 0.35:
            return "strong bearish trend"
        return "steady bearish trend"
    return "range-bound"


def select_option_strike(symbol: str, spot_price: float, direction: str) -> OptionSuggestion:
    option_type = "CE" if direction == "bullish" else "PE"
    rounded_spot = int(round(spot_price)) if spot_price > 0 else None
    label = f"ATM {option_type}" if rounded_spot is None else f"ATM / nearest {option_type} near {rounded_spot}"
    return OptionSuggestion(
        strike=None,
        option_type=option_type,
        label=label,
    )


def calculate_entry_exit(option_ltp: float, confidence_pct: int) -> dict[str, float]:
    strength = _trade_strength_from_confidence(confidence_pct)
    return calculate_trade_levels(option_ltp, strength=strength)


def calculate_premium_trade_levels(entry_price: float, confidence_pct: int) -> dict[str, float]:
    if entry_price <= 0:
        raise ValueError("entry_price must be greater than 0")

    if confidence_pct >= 80:
        target_multiplier = 1.20
        stop_loss_multiplier = 0.95
    elif confidence_pct >= 65:
        target_multiplier = 1.15
        stop_loss_multiplier = 0.93
    else:
        target_multiplier = 1.10
        stop_loss_multiplier = 0.90

    return {
        "entry_price": round(entry_price, 2),
        "target": round(entry_price * target_multiplier, 2),
        "stop_loss": round(entry_price * stop_loss_multiplier, 2),
    }


def calculate_trade_levels(ltp: float, strength: str = "moderate") -> dict[str, float]:
    if ltp <= 0:
        raise ValueError("ltp must be greater than 0")

    normalized_strength = strength.strip().lower()
    if normalized_strength == "strong":
        entry_discount = 0.02
        stop_loss_pct = 0.20
        target_pct = 0.60
    elif normalized_strength == "moderate":
        entry_discount = 0.035
        stop_loss_pct = 0.25
        target_pct = 0.50
    else:
        entry_discount = 0.05
        stop_loss_pct = 0.30
        target_pct = 0.40

    entry_high = round(ltp, 2)
    entry_low = round(ltp * (1 - entry_discount), 2)
    stop_loss = round(entry_low * (1 - stop_loss_pct), 2)
    target = round(entry_high * (1 + target_pct), 2)
    return {
        "entry_low": entry_low,
        "entry_high": entry_high,
        "stop_loss": stop_loss,
        "target": target,
    }


def enrich_signal_with_premium(signal: GeneratedSignal, premium: PremiumQuote | None) -> GeneratedSignal:
    if signal.details is None or signal.details.option_suggestion is None or premium is None:
        return signal

    premium_levels = calculate_premium_trade_levels(
        premium.last_price,
        confidence_pct=signal.details.confidence_pct,
    )
    option_label = signal.details.option_suggestion.label
    if premium.strike is not None and premium.option_type is not None:
        option_label = f"{premium.strike} {premium.option_type}"

    option = replace(
        signal.details.option_suggestion,
        strike=premium.strike,
        label=option_label,
        premium_ltp=round(premium.last_price, 2),
        trading_symbol=premium.trading_symbol,
        expiry=premium.expiry.isoformat() if premium.expiry is not None else None,
        entry_low=premium_levels["entry_price"],
        entry_high=premium_levels["entry_price"],
        stop_loss=premium_levels["stop_loss"],
        target=premium_levels["target"],
    )
    details = replace(signal.details, option_suggestion=option)
    return replace(
        signal,
        details=details,
        entry_price=premium_levels["entry_price"],
        target=premium_levels["target"],
        stop_loss=premium_levels["stop_loss"],
    )


def format_output(signal: GeneratedSignal) -> str:
    details = signal.details
    if details is None:
        return f"[SIGNAL] {signal.symbol} | {signal.signal} | Confidence: {int(round(signal.confidence * 100))}%"

    indicator = details.indicator_details
    option = details.option_suggestion
    divider = "-" * 40
    lines = [
        divider,
        f"TRADE SIGNAL - {signal.symbol}",
        divider,
        f"Action       : {details.action_label}",
        f"Confidence   : {details.confidence_pct}% ({details.confidence_label})",
    ]
    if details.risk_label != "Normal Entry":
        lines.append(f"Risk         : {details.risk_label}")

    lines.extend(
        [
            "",
            "Indicators:",
            f"EMA 9        : {_fmt(indicator.ema_9)}",
            f"EMA 21       : {_fmt(indicator.ema_21)}",
            f"RSI          : {_fmt(indicator.rsi)}{_rsi_suffix(indicator.rsi_state)}",
            f"Trend        : {indicator.trend.title()} ({indicator.market_condition.title()})",
        ]
    )
    if indicator.volume_ratio is not None:
        lines.append(f"Volume Ratio : {indicator.volume_ratio:.2f}x")

    lines.extend(["", "Validation:"])
    for item in details.validations:
        lines.append(f"{_status_icon(item.status)} {item.label}")

    if option is not None:
        lines.extend(["", "Option Trade Details:", f"Instrument   : {signal.symbol}", f"Strike       : {option.label}"])
        if option.trading_symbol:
            lines.append(f"Contract     : {option.trading_symbol}")
        if option.premium_ltp is not None:
            lines.append(f"Premium (LTP): {option.premium_ltp:.2f}")
        if option.entry_low is not None and option.entry_high is not None:
            lines.append(f"Entry Price  : {option.entry_low:.2f} - {option.entry_high:.2f}")
        if option.stop_loss is not None:
            lines.append(f"Stop Loss    : {option.stop_loss:.2f}")
        if option.target is not None:
            lines.append(f"Target       : {option.target:.2f}")

    lines.extend(["", f"Why          : {details.summary}", divider])
    return "\n".join(lines)


def print_signal(signal: GeneratedSignal) -> None:
    print(format_output(signal))


def _trend_strength_pct(ema_9: float, ema_21: float, last_price: float) -> float:
    if last_price <= 0:
        return 0.0
    return abs(ema_9 - ema_21) / last_price * 100.0


def _bullish_rsi_score(rsi: float) -> float:
    if 60.0 <= rsi <= 70.0:
        return 1.0
    if BULLISH_RSI_RANGE[0] <= rsi <= OVERBOUGHT_RSI:
        return 0.8
    if rsi > OVERBOUGHT_RSI:
        return 0.55
    return 0.30


def _bearish_rsi_score(rsi: float) -> float:
    if 30.0 <= rsi <= 40.0:
        return 1.0
    if OVERSOLD_RSI <= rsi <= BEARISH_RSI_RANGE[1]:
        return 0.8
    if rsi < OVERSOLD_RSI:
        return 0.55
    return 0.30


def _volume_ratio(candles: list[object]) -> float | None:
    volumes = [float(getattr(candle, "volume", 0) or 0) for candle in candles[-11:-1]]
    current_volume = float(getattr(candles[-1], "volume", 0) or 0)
    baseline = sum(volumes) / len(volumes) if volumes else 0.0
    if baseline <= 0:
        return None
    return round(current_volume / baseline, 2)


def _confidence_label(confidence_pct: int) -> str:
    if confidence_pct >= 80:
        return "High"
    if confidence_pct >= 65:
        return "Moderate"
    return "Low"


def _trade_strength_from_confidence(confidence_pct: int) -> str:
    if confidence_pct >= 80:
        return "strong"
    if confidence_pct >= 65:
        return "moderate"
    return "weak"


def _build_reason(
    trend: str,
    breakout: str,
    rsi_state: str,
    ema_9: float,
    ema_21: float,
    rsi: float,
    previous_high: float,
    previous_low: float,
) -> str:
    parts = [
        f"trend={trend}",
        breakout,
        f"rsi_state={rsi_state}",
        f"ema9={ema_9:.2f}",
        f"ema21={ema_21:.2f}",
        f"rsi={rsi:.2f}",
        f"breakout={previous_high:.2f}",
        f"breakdown={previous_low:.2f}",
    ]
    return " ".join(parts)


def _build_no_trade_reason(
    bullish_trend: bool,
    bearish_trend: bool,
    bullish_breakout: bool,
    bearish_breakout: bool,
    rsi: float,
    ema_9: float,
    ema_21: float,
    previous_high: float,
    previous_low: float,
) -> str:
    reasons: list[str] = []
    if not bullish_trend and not bearish_trend:
        reasons.append("trend=neutral")
    elif bullish_trend and not bullish_breakout:
        reasons.append("missing_bullish_breakout")
    elif bearish_trend and not bearish_breakout:
        reasons.append("missing_bearish_breakdown")
    if 45.0 < rsi < 55.0:
        reasons.append("rsi_mid_range")
    elif rsi > OVERBOUGHT_RSI:
        reasons.append("rsi_overbought_without_breakout")
    elif rsi < OVERSOLD_RSI:
        reasons.append("rsi_oversold_without_breakdown")
    reasons.extend(
        [
            f"ema9={ema_9:.2f}",
            f"ema21={ema_21:.2f}",
            f"rsi={rsi:.2f}",
            f"breakout={previous_high:.2f}",
            f"breakdown={previous_low:.2f}",
        ]
    )
    return " ".join(reasons or ["technical_filter_not_met"])


def _status_icon(status: str) -> str:
    if status == "pass":
        return "[OK]"
    if status == "warn":
        return "[!]"
    return "[X]"


def _rsi_suffix(rsi_state: str) -> str:
    if rsi_state == "overbought":
        return "  Overbought"
    if rsi_state == "oversold":
        return "  Oversold"
    return ""


def _fmt(value: float | None) -> str:
    if value is None:
        return "NA"
    return f"{value:.2f}"

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=False)

MARKET_DEFAULTS = {
    "EQUITY": {
        "preferred_min": 30.0,
        "preferred_max": 150.0,
        "hard_min": 20.0,
        "hard_max": 300.0,
        "max_iv": 32.0,
    },
    "MCX": {
        "preferred_min": 50.0,
        "preferred_max": 300.0,
        "hard_min": 20.0,
        "hard_max": 300.0,
        "max_iv": 38.0,
    },
}


@dataclass(frozen=True)
class OptionSelectionConfig:
    enable_mcx: bool = True
    enable_equity: bool = True
    min_premium: float = 50.0
    max_premium: float = 250.0
    stop_loss_percent: float = 0.20
    risk_reward_ratio: float = 2.0
    equity_max_iv: float = 32.0
    mcx_max_iv: float = 38.0


@dataclass(frozen=True)
class OptionCandidate:
    symbol: str
    strike: float
    option_type: str
    ltp: float
    oi: float
    oi_change: float
    volume: float
    iv: float | None
    price_change: float


@dataclass(frozen=True)
class RankedOption:
    option: OptionCandidate
    score: float
    strike_type: str
    confidence: str
    reason: str


def get_option_selection_config() -> OptionSelectionConfig:
    return OptionSelectionConfig(
        enable_mcx=_get_bool_env("ENABLE_MCX", True),
        enable_equity=_get_bool_env("ENABLE_EQUITY", True),
        min_premium=_get_float_env("MIN_PREMIUM", 50.0),
        max_premium=_get_float_env("MAX_PREMIUM", 250.0),
        stop_loss_percent=_get_float_env("OPTION_STOP_LOSS_PCT", 0.20),
        risk_reward_ratio=_get_float_env("OPTION_RR_RATIO", 2.0),
        equity_max_iv=_get_float_env("EQUITY_MAX_IV", 32.0),
        mcx_max_iv=_get_float_env("MCX_MAX_IV", 38.0),
    )


def get_atm_strike(ltp: float, strikes: list[float]) -> float:
    if ltp <= 0:
        raise ValueError("ltp must be greater than 0")
    if not strikes:
        raise ValueError("strikes list cannot be empty")

    return min(strikes, key=lambda strike: abs(float(strike) - ltp))


def filter_by_premium(
    options: list[dict[str, Any]] | list[OptionCandidate],
    market_type: str,
    config: OptionSelectionConfig | None = None,
) -> list[OptionCandidate]:
    normalized_market_type = _normalize_market_type(market_type)
    selection_config = config or get_option_selection_config()
    market_rules = MARKET_DEFAULTS[normalized_market_type]
    filtered: list[OptionCandidate] = []

    for option in _normalize_options(options):
        if option.ltp < market_rules["hard_min"]:
            logger.debug("Rejected %s: premium %.2f below hard minimum", option.symbol, option.ltp)
            continue
        if option.ltp > market_rules["hard_max"]:
            logger.debug("Rejected %s: premium %.2f above hard maximum", option.symbol, option.ltp)
            continue
        if option.ltp < selection_config.min_premium or option.ltp > selection_config.max_premium:
            logger.debug(
                "Rejected %s: premium %.2f outside configured range %.2f-%.2f",
                option.symbol,
                option.ltp,
                selection_config.min_premium,
                selection_config.max_premium,
            )
            continue

        filtered.append(option)

    return filtered


def analyze_oi(options: list[dict[str, Any]] | list[OptionCandidate]) -> dict[str, dict[str, Any]]:
    normalized_options = _normalize_options(options)
    if not normalized_options:
        return {}

    oi_values = [option.oi for option in normalized_options]
    volume_values = [option.volume for option in normalized_options]
    average_oi = _average(oi_values)
    average_volume = _average(volume_values)
    high_oi_threshold = _percentile(oi_values, 0.90)

    analysis: dict[str, dict[str, Any]] = {}
    for option in normalized_options:
        score = 0.0
        reason_parts: list[str] = []
        rejected = False

        if option.oi >= high_oi_threshold and high_oi_threshold > 0:
            rejected = True
            reason_parts.append("very_high_oi")
        elif option.oi >= average_oi and average_oi > 0:
            score += 1.0
            reason_parts.append("healthy_oi")

        if option.oi_change > 0:
            score += 1.5
            reason_parts.append("oi_buildup")

        if option.price_change > 0 and option.oi_change > 0:
            score += 1.5
            reason_parts.append("price_and_oi_rising")

        if option.volume >= average_volume * 1.25 and average_volume > 0:
            score += 2.0
            reason_parts.append("high_volume")
        elif option.volume >= average_volume * 0.80 and average_volume > 0:
            score += 1.0
            reason_parts.append("adequate_volume")
        else:
            reason_parts.append("low_volume")

        analysis[option.symbol] = {
            "score": score,
            "rejected": rejected,
            "average_oi": average_oi,
            "average_volume": average_volume,
            "high_oi_threshold": high_oi_threshold,
            "reason_parts": reason_parts,
        }

    return analysis


def select_best_option(
    options: list[dict[str, Any]] | list[OptionCandidate],
    signal: str,
    ltp: float,
    market_type: str,
    config: OptionSelectionConfig | None = None,
) -> RankedOption | None:
    normalized_signal = _normalize_signal(signal)
    normalized_market_type = _normalize_market_type(market_type)
    selection_config = config or get_option_selection_config()
    _validate_market_enabled(normalized_market_type, selection_config)

    if normalized_signal == "NO TRADE":
        logger.info("Signal is NO TRADE. Skipping option selection.")
        return None

    filtered_options = filter_by_premium(options, normalized_market_type, selection_config)
    if not filtered_options:
        logger.info("No options passed premium filters for market_type=%s", normalized_market_type)
        return None

    option_type = "CE" if normalized_signal == "BUY CALL" else "PE"
    same_side_options = [option for option in filtered_options if option.option_type == option_type]
    if not same_side_options:
        logger.info("No %s contracts available after premium filtering.", option_type)
        return None

    iv_limit = selection_config.equity_max_iv if normalized_market_type == "EQUITY" else selection_config.mcx_max_iv
    same_side_options = [option for option in same_side_options if option.iv is None or option.iv <= iv_limit]
    if not same_side_options:
        logger.info("No contracts passed IV filter. limit=%.2f", iv_limit)
        return None

    strikes = sorted({option.strike for option in same_side_options})
    atm_strike = get_atm_strike(ltp, strikes)
    strike_step = _infer_strike_step(strikes)
    preferred_strikes = _preferred_strikes(normalized_signal, atm_strike, strike_step)
    oi_analysis = analyze_oi(same_side_options)

    ranked_options: list[RankedOption] = []
    for option in same_side_options:
        if option.strike not in preferred_strikes:
            continue

        oi_result = oi_analysis.get(option.symbol, {})
        if oi_result.get("rejected"):
            logger.debug("Rejected %s due to OI analysis", option.symbol)
            continue

        score = _strike_score(option.strike, preferred_strikes)
        score += float(oi_result.get("score", 0.0))
        score += _premium_score(option.ltp, normalized_market_type)

        reason_parts = _build_reason_parts(
            option=option,
            signal=normalized_signal,
            ltp=ltp,
            atm_strike=atm_strike,
            oi_result=oi_result,
        )
        strike_type = _classify_strike_type(option.strike, ltp)
        confidence = _confidence_from_score(score)
        ranked_options.append(
            RankedOption(
                option=option,
                score=score,
                strike_type=strike_type,
                confidence=confidence,
                reason=", ".join(reason_parts),
            )
        )

    if not ranked_options:
        logger.info("No option matched strike selection logic for signal=%s", normalized_signal)
        return None

    best_option = max(ranked_options, key=lambda ranked: ranked.score)
    logger.info(
        "Selected option %s | strike=%.2f | premium=%.2f | confidence=%s",
        best_option.option.symbol,
        best_option.option.strike,
        best_option.option.ltp,
        best_option.confidence,
    )
    return best_option


def calculate_sl_target(
    premium: float,
    config: OptionSelectionConfig | None = None,
) -> tuple[float, float]:
    if premium <= 0:
        raise ValueError("premium must be greater than 0")

    selection_config = config or get_option_selection_config()
    stop_loss = _round_price(premium * (1 - selection_config.stop_loss_percent))
    risk = premium - stop_loss
    target = _round_price(premium + (risk * selection_config.risk_reward_ratio))
    return stop_loss, target


def generate_trade_signal(
    option: RankedOption | dict[str, Any] | OptionCandidate,
    signal: str,
    market_type: str,
    config: OptionSelectionConfig | None = None,
) -> dict[str, Any] | None:
    normalized_signal = _normalize_signal(signal)
    normalized_market_type = _normalize_market_type(market_type)
    selection_config = config or get_option_selection_config()
    _validate_market_enabled(normalized_market_type, selection_config)

    if normalized_signal == "NO TRADE":
        return None

    ranked_option = _coerce_ranked_option(option)
    stop_loss, target = calculate_sl_target(ranked_option.option.ltp, selection_config)
    entry_range = _entry_range(ranked_option.option.ltp)

    return {
        "symbol": ranked_option.option.symbol,
        "market_type": normalized_market_type,
        "signal": normalized_signal,
        "strike": int(ranked_option.option.strike) if ranked_option.option.strike.is_integer() else ranked_option.option.strike,
        "strike_type": ranked_option.strike_type,
        "premium": _round_price(ranked_option.option.ltp),
        "entry_range": entry_range,
        "stop_loss": stop_loss,
        "target": target,
        "confidence": ranked_option.confidence,
        "reason": ranked_option.reason,
    }


def select_option_trade(
    option_chain: list[dict[str, Any]],
    signal: str,
    market_type: str,
    ltp: float,
    config: OptionSelectionConfig | None = None,
) -> dict[str, Any] | None:
    best_option = select_best_option(option_chain, signal, ltp, market_type, config)
    if best_option is None:
        return None
    return generate_trade_signal(best_option, signal, market_type, config)


def _normalize_options(options: list[dict[str, Any]] | list[OptionCandidate]) -> list[OptionCandidate]:
    normalized: list[OptionCandidate] = []

    for item in options:
        if isinstance(item, OptionCandidate):
            normalized.append(item)
            continue

        strike = float(item["strike"])
        option_type = _normalize_option_type(str(item.get("type") or item.get("option_type") or ""))
        ltp = float(item.get("ltp") or item.get("premium") or item.get("last_price") or 0.0)
        symbol = str(item.get("symbol") or f"{int(strike)} {option_type}")
        oi = float(item.get("oi") or 0.0)
        oi_change = float(item.get("oi_change") or item.get("change_in_oi") or item.get("change_in_oi_value") or 0.0)
        volume = float(item.get("volume") or 0.0)
        iv_value = item.get("iv")
        iv = float(iv_value) if iv_value is not None else None
        price_change = float(item.get("price_change") or item.get("premium_change") or 0.0)

        normalized.append(
            OptionCandidate(
                symbol=symbol,
                strike=strike,
                option_type=option_type,
                ltp=ltp,
                oi=oi,
                oi_change=oi_change,
                volume=volume,
                iv=iv,
                price_change=price_change,
            )
        )

    return normalized


def _validate_market_enabled(market_type: str, config: OptionSelectionConfig) -> None:
    if market_type == "EQUITY" and not config.enable_equity:
        raise ValueError("EQUITY option selection is disabled via ENABLE_EQUITY")
    if market_type == "MCX" and not config.enable_mcx:
        raise ValueError("MCX option selection is disabled via ENABLE_MCX")


def _normalize_signal(signal: str) -> str:
    value = signal.strip().upper()
    if value in {"BUY_CE", "BUY CALL"}:
        return "BUY CALL"
    if value in {"BUY_PE", "BUY PUT"}:
        return "BUY PUT"
    return "NO TRADE"


def _normalize_market_type(market_type: str) -> str:
    value = market_type.strip().upper()
    if value not in MARKET_DEFAULTS:
        raise ValueError(f"Unsupported market type: {market_type}")
    return value


def _normalize_option_type(option_type: str) -> str:
    value = option_type.strip().upper()
    if value in {"CALL", "CE"}:
        return "CE"
    if value in {"PUT", "PE"}:
        return "PE"
    raise ValueError(f"Unsupported option type: {option_type}")


def _preferred_strikes(signal: str, atm_strike: float, strike_step: float) -> set[float]:
    if signal == "BUY CALL":
        return {atm_strike, atm_strike + strike_step}
    return {atm_strike, atm_strike - strike_step}


def _infer_strike_step(strikes: list[float]) -> float:
    if len(strikes) < 2:
        return 50.0

    deltas = [
        right - left
        for left, right in zip(strikes, strikes[1:])
        if (right - left) > 0
    ]
    return min(deltas) if deltas else 50.0


def _strike_score(strike: float, preferred_strikes: set[float]) -> float:
    ordered = sorted(preferred_strikes)
    if not ordered:
        return 0.0
    if strike == ordered[0]:
        return 4.0
    if len(ordered) > 1 and strike == ordered[1]:
        return 3.0
    return 0.0


def _premium_score(premium: float, market_type: str) -> float:
    market_rules = MARKET_DEFAULTS[market_type]
    if market_rules["preferred_min"] <= premium <= market_rules["preferred_max"]:
        return 2.0
    return 1.0


def _classify_strike_type(strike: float, ltp: float) -> str:
    if abs(strike - ltp) <= max(1.0, ltp * 0.001):
        return "ATM"
    if strike < ltp:
        return "ITM"
    return "OTM"


def _confidence_from_score(score: float) -> str:
    if score >= 8.0:
        return "HIGH"
    if score >= 5.0:
        return "MEDIUM"
    return "LOW"


def _build_reason_parts(
    option: OptionCandidate,
    signal: str,
    ltp: float,
    atm_strike: float,
    oi_result: dict[str, Any],
) -> list[str]:
    strike_label = "ATM" if option.strike == atm_strike else "near-ATM"
    direction_label = "call" if signal == "BUY CALL" else "put"
    parts = [f"{strike_label} {direction_label} selected"]

    if "oi_buildup" in oi_result.get("reason_parts", []):
        parts.append("OI buildup detected")
    if "price_and_oi_rising" in oi_result.get("reason_parts", []):
        parts.append("price and OI rising together")
    if "high_volume" in oi_result.get("reason_parts", []):
        parts.append("strong volume participation")
    elif "adequate_volume" in oi_result.get("reason_parts", []):
        parts.append("acceptable liquidity")

    if option.iv is not None:
        parts.append(f"IV {option.iv:.2f}")
    parts.append(f"spot {ltp:.2f}")
    return parts


def _coerce_ranked_option(option: RankedOption | dict[str, Any] | OptionCandidate) -> RankedOption:
    if isinstance(option, RankedOption):
        return option
    if isinstance(option, OptionCandidate):
        return RankedOption(
            option=option,
            score=0.0,
            strike_type=_classify_strike_type(option.strike, option.strike),
            confidence="LOW",
            reason="Option candidate supplied directly",
        )

    candidate = _normalize_options([option])[0]
    strike_type = str(option.get("strike_type") or _classify_strike_type(candidate.strike, candidate.strike))
    confidence = str(option.get("confidence") or "LOW")
    reason = str(option.get("reason") or "Option dictionary supplied directly")
    score = float(option.get("score") or 0.0)
    return RankedOption(
        option=candidate,
        score=score,
        strike_type=strike_type,
        confidence=confidence,
        reason=reason,
    )


def _entry_range(premium: float) -> list[float]:
    return [
        _round_price(premium * 0.98),
        _round_price(premium * 1.02),
    ]


def _average(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * percentile))))
    return ordered[index]


def _round_price(value: float) -> float:
    return round(value, 2)


def _get_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return float(value)
    except ValueError:
        logger.warning("Invalid float for %s=%s. Using default %.2f", name, value, default)
        return default


__all__ = [
    "OptionCandidate",
    "OptionSelectionConfig",
    "RankedOption",
    "analyze_oi",
    "calculate_sl_target",
    "filter_by_premium",
    "generate_trade_signal",
    "get_atm_strike",
    "get_option_selection_config",
    "select_best_option",
    "select_option_trade",
]

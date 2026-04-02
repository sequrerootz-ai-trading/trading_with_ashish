from __future__ import annotations
import logging
from dataclasses import replace
from collections import defaultdict, deque
from datetime import UTC, datetime
from time import perf_counter
from strategy.mcx_option_helper import enrich_mcx_signal_with_option
from strategy.signal_types import GeneratedSignal, SignalContext
from strategy.strategy_mcx import generate_mcx_signal

logger = logging.getLogger(__name__)

_daily_trade_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
_last_signal_direction: dict[str, str] = {}

SCALPING_MAX_TRADES_PER_DAY = 28        # was 22
STANDARD_MAX_TRADES_PER_DAY = 24        # was 18

SCALPING_CONFIDENCE_THRESHOLD = 0.40    # was 0.42
FAST_CONFIDENCE_THRESHOLD = 0.45        # was 0.47
DEFAULT_CONFIDENCE_THRESHOLD = 0.50     # was 0.52

STRICT_REJECTION_BUFFER = 0.15          # was 0.12

MIN_DIRECTION_FLIP_CONFIDENCE = 0.48    # keep same (perfect)

MIN_CANDLE_BODY_RATIO = 0.06            # keep same (perfect)

RELAXED_OPTION_MODE = True              # keep ON
FALLBACK_CONFIDENCE_THRESHOLD = 0.60
IV_DROP_TOLERANCE = 0.03
_OPTION_LTP_HISTORY: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=3))
_VWAP_CACHE: dict[tuple[str, str], float | None] = {}


def _is_valid_signal(signal: GeneratedSignal) -> bool:
    return signal is not None and signal.signal not in {"", None}


def _get_confidence_threshold(data: SignalContext) -> float:
    if data.timeframe_minutes <= 3:
        return SCALPING_CONFIDENCE_THRESHOLD
    if data.timeframe_minutes <= 5:
        return FAST_CONFIDENCE_THRESHOLD
    return DEFAULT_CONFIDENCE_THRESHOLD


def _passes_confidence_filter(signal: GeneratedSignal, data: SignalContext) -> tuple[bool, str]:
    threshold = _get_confidence_threshold(data)
    strict_floor = threshold - STRICT_REJECTION_BUFFER
    if signal.confidence < strict_floor:
        return False, "low_confidence_strict"
    if signal.confidence < threshold:
        return True, "weak_confidence_allowed"
    return True, "strong_confidence"


def _check_daily_trade_limit(symbol: str, data: SignalContext, max_trades: int = STANDARD_MAX_TRADES_PER_DAY) -> tuple[bool, str]:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    count = _daily_trade_counts[symbol][today]
    effective_limit = SCALPING_MAX_TRADES_PER_DAY if data.timeframe_minutes <= 3 else max(max_trades, STANDARD_MAX_TRADES_PER_DAY)
    if count >= effective_limit:
        return False, f"daily_limit_reached_{count}"
    return True, f"trades_today_{count}"


def _record_signal_fired(symbol: str, signal_type: str) -> None:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    _daily_trade_counts[symbol][today] += 1
    _last_signal_direction[symbol] = signal_type


def _has_strong_candle(data: SignalContext) -> bool:
    candle = data.last_candle
    if candle is None:
        return False
    candle_range = max(float(candle.high) - float(candle.low), 0.0)
    if candle_range <= 0:
        return False
    body = abs(float(candle.close) - float(candle.open))
    return body >= (candle_range * MIN_CANDLE_BODY_RATIO)


def _compute_vwap(data: SignalContext) -> float | None:
    if not data.candles or data.last_candle is None:
        return None

    cache_key = (data.symbol, data.last_candle.end.isoformat())
    cached_vwap = _VWAP_CACHE.get(cache_key)
    if cached_vwap is not None or cache_key in _VWAP_CACHE:
        return cached_vwap

    today = datetime.now(UTC).date()
    total_price_volume = 0.0
    total_volume = 0.0
    for candle in data.candles:
        candle_time = getattr(candle, "timestamp", None) or getattr(candle, "end", None)
        if candle_time is None or candle_time.date() != today:
            continue

        volume = _safe_float(getattr(candle, 'volume', None), 0.0) or 0.0
        if volume <= 0:
            continue
        typical_price = (float(candle.high) + float(candle.low) + float(candle.close)) / 3.0
        total_price_volume += typical_price * volume
        total_volume += volume

    vwap = None if total_volume <= 0 else (total_price_volume / total_volume)
    _VWAP_CACHE[cache_key] = vwap
    return vwap


def _passes_direction_flip_control(symbol: str, signal: GeneratedSignal) -> tuple[bool, str]:
    previous_signal = _last_signal_direction.get(symbol)
    if previous_signal is None or previous_signal == signal.signal:
        return True, "direction_ok"
    if signal.confidence >= MIN_DIRECTION_FLIP_CONFIDENCE:
        return True, "direction_flip_strong"
    return False, "direction_flip_weak"


def _option_chain_score(option_chain, signal_type):
    if not option_chain:
        return True, "option_data_unavailable"

    normalized_signal = str(signal_type or "").strip().upper()
    option_type = "CE" if normalized_signal == "BUY" else "PE" if normalized_signal == "SELL" else None
    if option_type is None:
        return False, "weak_option_flow"

    price_changes: list[float] = []
    oi_changes: list[float] = []
    for item in option_chain:
        item_option_type = str(item.get("type") or item.get("option_type") or "").strip().upper()
        if item_option_type != option_type:
            continue

        ltp = _safe_float(item.get("ltp") or item.get("premium") or item.get("last_price"), 0.0) or 0.0
        previous_ltp = _safe_float(item.get("previous_ltp") or item.get("prev_ltp") or item.get("close") or item.get("previous_close"))
        oi_change = _safe_float(item.get("oi_change") or item.get("change_in_oi"), 0.0) or 0.0
        if ltp <= 0:
            continue
        if previous_ltp is not None:
            price_changes.append(ltp - previous_ltp)
        oi_changes.append(oi_change)

    if not oi_changes:
        return True, "option_data_unavailable"

    avg_price_change = (sum(price_changes) / len(price_changes)) if price_changes else 0.0
    avg_oi_change = sum(oi_changes) / len(oi_changes)

    if avg_price_change > 0 and avg_oi_change > 0:
        return True, "strong_option_flow"
    if avg_price_change < 0 and avg_oi_change <= 0:
        return False, "long_unwinding"
    if avg_price_change > 0 and avg_oi_change <= 0:
        return False, "short_covering"
    return False, "weak_option_flow"


def select_best_option(option_chain: list[dict], signal_type: str, spot_price: float) -> dict | None:
    if not option_chain:
        return None

    normalized_signal = str(signal_type or "").strip().upper()
    option_type = "CE" if normalized_signal == "BUY" else "PE" if normalized_signal == "SELL" else None
    if option_type is None:
        return None

    strikes = sorted({int(round(float(item.get("strike") or 0))) for item in option_chain if float(item.get("strike") or 0) > 0})
    if not strikes:
        return None

    atm_strike = min(strikes, key=lambda strike: abs(strike - float(spot_price)))
    deltas = sorted({right - left for left, right in zip(strikes, strikes[1:]) if (right - left) > 0})
    strike_step = deltas[0] if deltas else 100
    allowed_distance = strike_step * 3

    candidates = []
    for item in option_chain:
        try:
            strike = int(round(float(item.get("strike") or 0)))
            ltp = float(item.get("ltp") or item.get("premium") or item.get("last_price") or 0.0)
            volume = float(item.get("volume") or 0.0)
            oi_change = float(item.get("oi_change") or item.get("change_in_oi") or 0.0)
        except (TypeError, ValueError):
            continue

        item_type = str(item.get("type") or item.get("option_type") or "").strip().upper()
        if item_type != option_type or strike <= 0 or ltp <= 0:
            continue
        if abs(strike - atm_strike) > allowed_distance:
            continue

        bid = _safe_float(item.get("bid") or item.get("buy_price") or item.get("best_bid"))
        ask = _safe_float(item.get("ask") or item.get("sell_price") or item.get("best_ask"))
        spread = (ask - bid) if bid is not None and ask is not None and ask >= bid else 0.0
        spread_ok = True if bid is None or ask is None or ltp <= 0 else (spread / ltp) < 0.08
        if ltp < 30 or volume < 5 or not spread_ok:
            continue

        previous_ltp = _safe_float(item.get("previous_ltp") or item.get("prev_ltp") or item.get("close") or item.get("previous_close"))
        price_change = _safe_float(item.get("change"), 0.0) or 0.0
        has_price_reference = previous_ltp is not None or abs(price_change) > 0
        premium_rising = (previous_ltp is not None and ltp > previous_ltp) or price_change > 0
        if has_price_reference and not premium_rising:
            continue

        iv = _safe_float(item.get("iv") or item.get("implied_volatility"))
        previous_iv = _safe_float(item.get("previous_iv") or item.get("prev_iv"))
        iv_ok = True if iv is None or previous_iv is None else iv >= (previous_iv * (1 - IV_DROP_TOLERANCE))
        if not iv_ok:
            continue

        candidates.append({
            **item,
            "strike": strike,
            "option_type": item_type,
            "ltp": round(ltp, 2),
            "previous_ltp": previous_ltp,
            "volume": volume,
            "oi_change": oi_change,
            "_spread_ok": spread_ok,
            "_iv_ok": iv_ok,
            "_premium_rising": premium_rising,
        })

    if not candidates:
        return None

    average_volume = sum(float(item["volume"]) for item in candidates) / len(candidates)
    best_option = None
    best_score = -1
    best_distance = float("inf")

    for item in candidates:
        score = 0
        if float(item["oi_change"]) > 0:
            score += 1
        if float(item["volume"]) > average_volume:
            score += 1
        if bool(item["_spread_ok"]):
            score += 1
        if bool(item["_iv_ok"]):
            score += 1

        distance = abs(int(item["strike"]) - atm_strike)
        if score > best_score or (score == best_score and distance < best_distance):
            best_option = {**item, "selection_score": score}
            best_score = score
            best_distance = distance

    return best_option


def _is_premium_trending_up(option_data: dict[str, object] | None) -> bool:
    if not option_data:
        return False

    option_key = str(option_data.get("tradingsymbol") or f"{option_data.get('strike')}_{option_data.get('option_type')}")
    current_ltp = _safe_float(option_data.get("ltp") or option_data.get("premium") or option_data.get("last_price"), 0.0) or 0.0
    if current_ltp <= 0:
        return False

    history = _OPTION_LTP_HISTORY[option_key]
    history.append(round(current_ltp, 2))

    provided_history = option_data.get("ltp_history") or option_data.get("price_history") or []
    normalized_history = [value for value in (_safe_float(value) for value in provided_history) if value is not None]
    series = normalized_history[-2:] + history if normalized_history else history
    if len(series) >= 3:
        return series[-1] > series[-2] >= series[-3]
    if len(series) >= 2:
        return series[-1] > series[-2]

    previous_ltp = _safe_float(option_data.get("previous_ltp") or option_data.get("prev_ltp") or option_data.get("close") or option_data.get("previous_close"))
    return previous_ltp is not None and current_ltp > previous_ltp


def _is_iv_confirmed(option_data: dict[str, object] | None) -> bool:
    if not option_data:
        return False

    iv = _safe_float(option_data.get("iv") or option_data.get("implied_volatility"))
    previous_iv = _safe_float(option_data.get("previous_iv") or option_data.get("prev_iv"))
    if iv is None or previous_iv is None:
        return True
    return iv >= (previous_iv * (1 - IV_DROP_TOLERANCE))


def generate_mcx_signal_engine(
    symbol: str,
    data: SignalContext,
    sentiment: dict[str, object] | None = None,
    max_trades_per_day: int = STANDARD_MAX_TRADES_PER_DAY,
) -> GeneratedSignal:
    latency_start = perf_counter()
    try:
        now_ts = datetime.now(UTC).isoformat()
        last_candle = data.last_candle
        spot_price = float(last_candle.close) if last_candle else 0.0
        option_chain = (sentiment or {}).get("option_chain")

        limit_ok, limit_reason = _check_daily_trade_limit(symbol, data, max_trades=max_trades_per_day)
        if not limit_ok:
            return GeneratedSignal(symbol, now_ts, "NO_TRADE", limit_reason, 0.0)

        try:
            confidence_reasons: list[str] = []
            fallback_logged = False
            best_option: dict[str, object] | None = None
            premium_rising = False
            premium_trending = False
            iv_confirmed = True
            signal = generate_mcx_signal(symbol, data)
            signal = enrich_mcx_signal_with_option(
                symbol=symbol,
                generated_signal=signal,
                spot_price=spot_price,
                option_chain=option_chain,
            )
            option_data = signal.context or {}
            confidence_boost = _safe_float(option_data.get("confidence_boost"), 0.0) or 0.0
            if not bool(option_data.get("option_available")):
                logger.info("[FALLBACK] Option confirmation unavailable -> using underlying-only signal")
                fallback_logged = True
                if RELAXED_OPTION_MODE:
                    adjusted_confidence = max(0.0, min(1.0, signal.confidence + (confidence_boost * 0.02)))
                    signal = replace(signal, confidence=adjusted_confidence)
                    confidence_reasons.append("Fallback: Option data not available")
                else:
                    return GeneratedSignal(symbol, now_ts, "NO_TRADE", "no_valid_option", signal.confidence)
            else:
                adjusted_confidence = max(0.0, min(1.0, signal.confidence + (confidence_boost * 0.02)))
                signal = replace(signal, confidence=adjusted_confidence)
                confidence_reasons.append("Option confirmation available")

            flow_ok, flow_reason = _option_chain_score(option_chain, signal.signal)
            if not flow_ok:
                adjusted_confidence = max(0.0, min(1.0, signal.confidence - 0.05))
                signal = replace(signal, confidence=adjusted_confidence)

            best_option = select_best_option(option_chain, signal.signal, spot_price)
            if best_option is None:
                if not RELAXED_OPTION_MODE:
                    return GeneratedSignal(symbol, now_ts, "NO_TRADE", "no_valid_option", signal.confidence)
                if not fallback_logged:
                    logger.info("[FALLBACK] No valid option contract -> using underlying-only signal")
                if "Fallback: Option data not available" not in confidence_reasons:
                    confidence_reasons.append("Fallback: Option data not available")
            else:
                premium_rising = bool(best_option.get("_premium_rising"))
                premium_trending = _is_premium_trending_up(best_option)
                iv_confirmed = _is_iv_confirmed(best_option)
                signal = enrich_mcx_signal_with_option(
                    symbol=symbol,
                    generated_signal=signal,
                    spot_price=spot_price,
                    option_chain=[best_option],
                )
                confidence_reasons.append("Option contract selected")
                logger.info(
                    "[OPTION_SELECTED] strike=%s | type=%s | score=%s",
                    best_option.get("strike"),
                    best_option.get("option_type") or best_option.get("type"),
                    best_option.get("selection_score"),
                )
        except Exception as exc:
            logger.error("[MCX_STRATEGY_ERROR] %s - %s", symbol, exc)
            return GeneratedSignal(symbol, now_ts, "NO_TRADE", "strategy_exception", 0.0)

        if signal is None:
            return GeneratedSignal(symbol, now_ts, "NO_TRADE", "invalid_signal", 0.0)
        if signal.signal == "NO_TRADE":
            return signal
        if not _is_valid_signal(signal):
            return GeneratedSignal(symbol, now_ts, "NO_TRADE", "invalid_signal", signal.confidence)

        if not _has_strong_candle(data):
            return GeneratedSignal(symbol, now_ts, "NO_TRADE", "weak_candle", 0.0)

        direction_ok, direction_reason = _passes_direction_flip_control(symbol, signal)
        if not direction_ok:
            return GeneratedSignal(symbol, now_ts, "NO_TRADE", direction_reason, signal.confidence)

        strength = 0
        price = spot_price
        vwap = _compute_vwap(data)
        signal_type = "CALL" if signal.signal == "BUY" else "PUT" if signal.signal == "SELL" else None
        if vwap and vwap > 0:
            if signal_type == "CALL":
                if price > vwap:
                    strength += 2
                else:
                    strength -= 1
            elif signal_type == "PUT":
                if price < vwap:
                    strength += 2
                else:
                    strength -= 1

        adjusted_confidence = signal.confidence + (strength * 0.02)
        adjusted_confidence = max(0.0, min(1.0, adjusted_confidence))
        signal = replace(signal, confidence=adjusted_confidence)

        passes, confidence_reason = _passes_confidence_filter(signal, data)
        if not passes:
            adjusted_confidence = max(0.0, min(1.0, signal.confidence - 0.05))
            signal = replace(signal, confidence=adjusted_confidence)

        if best_option is not None:
            if not premium_rising:
                logger.info("[REJECTED] %s premium falling", signal_type or "OPTION")
                return GeneratedSignal(symbol, now_ts, "NO_TRADE", "option_not_confirmed", signal.confidence)
            if not premium_trending:
                logger.info("[REJECTED] weak premium trend")
                return GeneratedSignal(symbol, now_ts, "NO_TRADE", "premium_not_trending", signal.confidence)
            if not iv_confirmed:
                logger.info("[REJECTED] IV dropping")
                return GeneratedSignal(symbol, now_ts, "NO_TRADE", "option_not_confirmed", signal.confidence)
            if signal_type == "CALL" and vwap and not (price > vwap and premium_rising):
                logger.info("[REJECTED] CALL premium not aligned with VWAP")
                return GeneratedSignal(symbol, now_ts, "NO_TRADE", "option_not_confirmed", signal.confidence)
            if signal_type == "PUT" and vwap and not (price < vwap and premium_rising):
                logger.info("[REJECTED] PUT premium not aligned with VWAP")
                return GeneratedSignal(symbol, now_ts, "NO_TRADE", "option_not_confirmed", signal.confidence)
            logger.info("[CONFIRMED] strong premium breakout")

        final_score = 0.0
        final_score += signal.confidence * 5
        final_score += strength
        if flow_ok:
            final_score += 2
        else:
            final_score -= 1

        if final_score < 2.5:
            return GeneratedSignal(symbol, now_ts, "NO_TRADE", "low_final_score", signal.confidence)

        signal = replace(
            signal,
            context={
                **(signal.context or {}),
                "vwap": round(vwap, 2) if vwap and vwap > 0 else None,
                "vwap_strength": int(strength),
                "final_score": round(final_score, 2),
                "flow_ok": flow_ok,
            },
        )
        logger.info("[CONFIDENCE] Score=%.2f | Reasons=%s", signal.confidence, confidence_reasons or ["base_signal"])

        _record_signal_fired(symbol, signal.signal)
        reason = direction_reason if direction_reason != "direction_ok" else confidence_reason
        logger.info("[MCX_SIGNAL] %s | %s | conf=%.2f | %s", symbol, signal.signal, signal.confidence, reason)
        return signal
    finally:
        logger.info("[MCX_LATENCY] %s | %.2fms", symbol, (perf_counter() - latency_start) * 1000.0)

def _safe_float(value, default: float | None = None) -> float | None:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default

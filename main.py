from __future__ import annotations

import logging
import os
from dataclasses import replace
from datetime import date, datetime, time as dt_time

from utils_console import CYAN, GREEN, RED, YELLOW, colorize
from config import get_mode
from config.settings import get_settings
from data.candle_manager import CandleManager
from data.data_loader import HistoricalDataLoader, MINIMUM_STARTUP_CANDLES
from data.database import TradingDatabase
from data.market_data import MarketDataService
from data.option_premium import OptionPremiumService
from execution.order_manager import OrderManager
from execution.trade_manager import ActiveTrade, TradeManager
from strategy.equity_decision_engine import enrich_signal_with_premium
from strategy.signal_engine import store_market_data, store_signal
from strategy.strategy import LastClosedCandleStrategy, MINIMUM_INDICATOR_CANDLES


DAILY_STATE = {
    "date": None,
    "daily_pnl": 0.0,
    "completed_trades": 0,
    "cooldown_candles": 0,
}


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    logging.getLogger("data.market_data").setLevel(logging.INFO)


def main() -> None:
    configure_logging()
    mode = get_mode()
    settings = get_settings()
    symbol = settings.symbol
    market_type = settings.market_type
    instrument = settings.instruments[0]

    logging.info("Runtime MODE=%s", mode)
    logging.info("Runtime MARKET_TYPE=%s", market_type)
    logging.info("[INFO] Processing SYMBOL: %s", symbol)
    logging.info("[INFO] Sentiment and news integrations disabled for fast execution.")

    market_data = MarketDataService(settings=settings)
    database = TradingDatabase()
    candle_manager = CandleManager(max_candles=100)
    loader = HistoricalDataLoader(market_data, database)
    premium_service = OptionPremiumService(market_data.clients.kite, market_type=market_type)
    trade_manager = TradeManager()
    order_manager = OrderManager(
        market_data.clients.kite,
        settings.execution,
        instrument=instrument,
        trade_manager=trade_manager,
    )

    historical_result = loader.fetch_historical_candles(symbol, market_type=market_type)
    candle_manager.initialize_candles(symbol, historical_result)
    if len(historical_result) < MINIMUM_INDICATOR_CANDLES:
        logging.warning(
            "[INFO] Processing SYMBOL: %s | Historical backfill still warming up | loaded=%s candles | required=%s",
            symbol,
            len(historical_result),
            MINIMUM_INDICATOR_CANDLES,
        )
    elif len(historical_result) < MINIMUM_STARTUP_CANDLES:
        logging.info(
            "[INFO] Processing SYMBOL: %s | Historical backfill ready for signals | loaded=%s candles | target=%s",
            symbol,
            len(historical_result),
            MINIMUM_STARTUP_CANDLES,
        )
    else:
        logging.info(
            "[INFO] Processing SYMBOL: %s | Historical backfill ready | candles=%s | last_completed=%s",
            symbol,
            len(historical_result),
            historical_result[-1].end.strftime("%Y-%m-%d %H:%M") if historical_result else "NA",
        )

    strategy = LastClosedCandleStrategy(candle_manager, symbol, market_type, timeframe_minutes=settings.candle_interval_minutes)

    generated_signal = strategy.evaluate(_default_sentiment())
    if generated_signal is not None:
        try:
            store_signal(generated_signal, database)
        except Exception as exc:
            logging.warning("Failed to store startup signal in DB: %s", exc)

        last_completed = candle_manager.get_last_completed_candle(symbol)
        if last_completed is not None:
            logging.info(
                "[INFO] Processing SYMBOL: %s | Startup analysis ready | last_completed=%s | signal=%s",
                symbol,
                last_completed.end.strftime("%Y-%m-%d %H:%M"),
                generated_signal.signal,
            )
        _handle_generated_signal(symbol, generated_signal, premium_service, last_completed.close if last_completed else 0.0, trade_manager, order_manager, candle_manager)

    def handle_closed_candle(candle) -> None:
        candle_manager.on_new_closed_candle(candle)
        try:
            inserted = store_market_data(candle, database)
            if inserted:
                logging.info(
                    "[INFO] Processing SYMBOL: %s | Stored closed candle | timestamp=%s",
                    symbol,
                    candle.end.strftime("%Y-%m-%d %H:%M"),
                )
            else:
                logging.info(
                    "[INFO] Processing SYMBOL: %s | Reused stored candle | timestamp=%s",
                    symbol,
                    candle.end.strftime("%Y-%m-%d %H:%M"),
                )
        except Exception as exc:
            logging.warning("DB failed for market data, using in-memory buffer only: %s", exc)

        _manage_active_trade(symbol, trade_manager, premium_service, order_manager)

        generated = strategy.evaluate(_default_sentiment())
        if generated is None:
            return

        try:
            store_signal(generated, database)
        except Exception as exc:
            logging.warning("Failed to store signal in DB: %s", exc)

        _handle_generated_signal(symbol, generated, premium_service, candle.close, trade_manager, order_manager, candle_manager)

    market_data.on_candle = handle_closed_candle

    logging.info(
        "Engine initialized | mode=%s | market_type=%s | symbol=%s | candle=%s min",
        mode,
        market_type,
        symbol,
        settings.candle_interval_minutes,
    )
    logging.info(
        "Historical backfill completed. Startup analysis finished using last completed candle. exchange=%s",
        instrument.exchange,
    )
    print(colorize(f"[WAIT] Processing SYMBOL: {symbol} | waiting for live ticks and the next {settings.candle_interval_minutes}-minute candle close...", YELLOW, bold=True))

    market_data.start()


def _handle_generated_signal(symbol: str, generated_signal, premium_service, spot_price: float, trade_manager: TradeManager, order_manager: OrderManager, candle_manager: CandleManager) -> None:
    _reset_daily_state_if_needed()
    if trade_manager.has_active_trade(symbol):
        print(colorize("[SKIPPED] Active trade already exists", YELLOW, bold=True))
        return
    if not _is_trade_window_open(symbol):
        _print_skip("Time filter")
        return
    if _daily_loss_limit_reached():
        _print_blocked("Daily loss limit reached")
        return
    if _max_trades_reached():
        _print_blocked("Max trades reached")
        return
    if _cooldown_active():
        DAILY_STATE["cooldown_candles"] = max(int(DAILY_STATE["cooldown_candles"]) - 1, 0)
        _print_skip("Trade cooldown active")
        return

    if generated_signal.signal == "BUY_CE":
        premium = _safe_premium_quote(premium_service, symbol, spot_price, "CALL")
        if premium is None:
            _print_premium_unavailable(symbol, "CE")
            return
        enriched_signal = enrich_signal_with_premium(generated_signal, premium)
        if _should_skip_trade(symbol, enriched_signal, premium.last_price, candle_manager):
            return
        _register_trade_plan(symbol, enriched_signal, premium, trade_manager)
        _print_signal(enriched_signal)
    elif generated_signal.signal == "BUY_PE":
        premium = _safe_premium_quote(premium_service, symbol, spot_price, "PUT")
        if premium is None:
            _print_premium_unavailable(symbol, "PE")
            return
        enriched_signal = enrich_signal_with_premium(generated_signal, premium)
        if _should_skip_trade(symbol, enriched_signal, premium.last_price, candle_manager):
            return
        _register_trade_plan(symbol, enriched_signal, premium, trade_manager)
        _print_signal(enriched_signal)
    elif generated_signal.signal in {"BUY", "SELL"}:
        _print_signal(generated_signal)
    else:
        _log_no_trade(symbol, generated_signal.reason)


def _safe_premium_quote(premium_service, symbol: str, spot_price: float, signal: str):
    try:
        return premium_service.get_premium_quote(symbol, spot_price, signal)
    except Exception as exc:
        logging.warning("Premium lookup failed for %s: %s", symbol, exc)
        return None


def _print_premium_unavailable(symbol: str, option_type: str) -> None:
    logging.warning("Unable to fetch option premium for %s %s", symbol, option_type)
    print(colorize(f"[ERROR] Unable to fetch option premium for {symbol} {option_type}", RED, bold=True))


def _register_trade_plan(symbol: str, generated_signal, premium, trade_manager: TradeManager) -> ActiveTrade | None:
    if generated_signal.details is None or generated_signal.details.option_suggestion is None:
        return None
    option = generated_signal.details.option_suggestion
    if option.entry_low is None or option.entry_high is None or option.stop_loss is None:
        return None
    if trade_manager.has_active_trade(symbol):
        logging.info("Skipping signal for %s because one active trade already exists.", symbol)
        return None
    return trade_manager.open_trade_plan(
        symbol=symbol,
        signal=generated_signal.signal,
        trading_symbol=premium.trading_symbol,
        exchange=premium.exchange,
        option_type=premium.option_type or option.option_type,
        entry_low=option.entry_low,
        entry_high=option.entry_high,
        stop_loss=option.stop_loss,
        entry_price=None,
    )


def _manage_active_trade(symbol: str, trade_manager: TradeManager, premium_service, order_manager: OrderManager) -> bool:
    _reset_daily_state_if_needed()
    active_trade = trade_manager.get_active_trade(symbol)
    if active_trade is None:
        return False

    try:
        premium = premium_service.get_contract_quote(active_trade.trading_symbol, active_trade.exchange)
    except Exception as exc:
        logging.warning("Unable to refresh premium for active trade %s: %s", active_trade.trading_symbol, exc)
        _print_order_error(f"Premium refresh failed for {active_trade.trading_symbol}: {exc}")
        return True

    if premium is None:
        logging.warning("Unable to refresh premium for active trade %s", active_trade.trading_symbol)
        return True

    current_price = premium.last_price
    if active_trade.status == "PENDING_ENTRY":
        if current_price < active_trade.initial_stop_loss:
            closed_trade = trade_manager.close_active_trade(symbol, "entry_failed_below_stop", current_price)
            if closed_trade is not None:
                print(colorize(
                    "\n".join([
                        "[ENTRY CANCELLED]",
                        f"{closed_trade.trading_symbol} slipped below stop before entry.",
                        f"LTP: {_fmt_rupee(current_price)} | Planned SL: {_fmt_rupee(closed_trade.initial_stop_loss)}",
                    ]),
                    RED,
                    bold=True,
                ))
            return True

        if active_trade.entry_low <= current_price <= active_trade.entry_high:
            updated_trade = _try_execute_entry_if_needed(symbol, active_trade, current_price, trade_manager, order_manager)
            if updated_trade is not None and updated_trade.status == "OPEN":
                _print_trade_running(updated_trade, current_price)
            return True

        _print_trade_waiting(active_trade, current_price)
        return True

    if current_price <= active_trade.stop_loss:
        exit_order = _safe_exit_position(active_trade, current_price, order_manager)
        if exit_order is None:
            return True
        exit_price = _extract_order_price(exit_order, current_price)
        closed_trade = trade_manager.close_active_trade(symbol, "stop_loss_hit", exit_price)
        if closed_trade is not None:
            _record_trade_result(closed_trade, exit_price)
            _print_stop_loss_hit(closed_trade, exit_price)
        return True

    updated_trade = _trail_active_trade_if_needed(active_trade, current_price, trade_manager, order_manager)
    _print_trade_running(updated_trade or active_trade, current_price)
    return True


def _try_execute_entry_if_needed(symbol: str, active_trade: ActiveTrade, current_price: float, trade_manager: TradeManager, order_manager: OrderManager) -> ActiveTrade | None:
    latest_trade = trade_manager.get_active_trade(symbol) or active_trade
    if latest_trade.order_placed or latest_trade.status != "PENDING_ENTRY":
        return latest_trade
    if not (latest_trade.entry_low <= current_price <= latest_trade.entry_high):
        return latest_trade

    original_settings = order_manager.settings
    try:
        order_manager.settings = replace(
            original_settings,
            capital_per_trade=_risk_based_capital(order_manager, original_settings.capital_per_trade, current_price, latest_trade.stop_loss),
        )
        managed_order = order_manager.place_market_buy(
            trading_symbol=latest_trade.trading_symbol,
            exchange=latest_trade.exchange,
            last_price=current_price,
            stop_loss_price=latest_trade.stop_loss,
        )
        updated_trade = trade_manager.update_active_trade(
            symbol=symbol,
            status="OPEN",
            entry_price=managed_order.entry_price,
            stop_loss=managed_order.stop_loss_price,
            initial_stop_loss=latest_trade.initial_stop_loss,
            quantity=managed_order.quantity,
            highest_price=managed_order.entry_price,
            order_placed=True,
            entry_order_id=managed_order.entry_order_id,
            stop_loss_order_id=managed_order.stop_loss_order_id,
        )
        if updated_trade is not None:
            _print_trade_started(updated_trade)
        return updated_trade
    except Exception as exc:
        logging.exception("Entry order failed for %s", latest_trade.trading_symbol)
        _print_order_error(f"Order failed for {latest_trade.trading_symbol}: {exc}")
        trade_manager.close_active_trade(symbol, "entry_order_failed", current_price)
        return None
    finally:
        order_manager.settings = original_settings


def _handle_live_stop_loss_completion(symbol: str, active_trade: ActiveTrade, current_price: float, trade_manager: TradeManager, order_manager: OrderManager) -> bool:
    if order_manager.mode != "LIVE" or not active_trade.stop_loss_order_id:
        return False

    try:
        stop_order = order_manager.check_order_status(active_trade.stop_loss_order_id)
    except Exception as exc:
        logging.warning("Unable to check stop-loss order status for %s: %s", active_trade.trading_symbol, exc)
        _print_order_error(f"Could not verify stop-loss order for {active_trade.trading_symbol}: {exc}")
        return False

    stop_status = str(stop_order.get("status", "UNKNOWN")).upper()
    if stop_status == "COMPLETE":
        exit_price = _extract_order_price(stop_order, active_trade.stop_loss)
        order_manager.trade_manager.record_trade(
            mode="LIVE",
            symbol=active_trade.trading_symbol,
            side="SELL",
            quantity=active_trade.quantity,
            price=exit_price,
            status="STOP_LOSS_FILLED",
        )
        closed_trade = trade_manager.close_active_trade(symbol, "stop_loss_hit", exit_price)
        if closed_trade is not None:
            _record_trade_result(closed_trade, exit_price)
            _print_stop_loss_hit(closed_trade, exit_price)
        return True

    if stop_status in {"REJECTED", "CANCELLED"} and current_price <= active_trade.stop_loss:
        _print_order_error(f"Stop-loss order {stop_status.lower()} for {active_trade.trading_symbol}. Exiting at market.")
        exit_order = _safe_exit_position(active_trade, current_price, order_manager)
        if exit_order is None:
            return True
        exit_price = _extract_order_price(exit_order, current_price)
        closed_trade = trade_manager.close_active_trade(symbol, "emergency_stop_loss_exit", exit_price)
        if closed_trade is not None:
            _record_trade_result(closed_trade, exit_price)
            _print_stop_loss_hit(closed_trade, exit_price)
        return True

    return False


def _safe_exit_position(active_trade: ActiveTrade, current_price: float, order_manager: OrderManager):
    try:
        return order_manager.exit_position(
            trading_symbol=active_trade.trading_symbol,
            exchange=active_trade.exchange,
            quantity=active_trade.quantity,
            last_price=current_price,
        )
    except Exception as exc:
        logging.exception("Exit order failed for %s", active_trade.trading_symbol)
        _print_order_error(f"Exit failed for {active_trade.trading_symbol}: {exc}")
        return None


def _trail_active_trade_if_needed(active_trade: ActiveTrade, current_price: float, trade_manager: TradeManager, order_manager: OrderManager) -> ActiveTrade | None:
    entry_price = active_trade.entry_price
    if entry_price is None or active_trade.quantity <= 0:
        return active_trade

    highest_price = max(active_trade.highest_price or entry_price, current_price)
    profit_pct = ((current_price - entry_price) / entry_price) if entry_price > 0 else 0.0

    new_stop_loss = active_trade.stop_loss
    if profit_pct >= 0.10:
        new_stop_loss = max(new_stop_loss, active_trade.entry_low)
    if profit_pct >= 0.25:
        new_stop_loss = max(new_stop_loss, entry_price)
    if profit_pct >= 0.40:
        new_stop_loss = max(new_stop_loss, entry_price * 1.20)

    if highest_price <= (active_trade.highest_price or entry_price) and new_stop_loss <= active_trade.stop_loss:
        return active_trade

    if new_stop_loss <= active_trade.stop_loss:
        return trade_manager.update_active_trade(symbol=active_trade.symbol, highest_price=highest_price)

    broker_stop_loss = new_stop_loss
    if order_manager.mode == "LIVE" and active_trade.stop_loss_order_id:
        try:
            broker_stop_loss = order_manager.trail_stop_loss_to_price(
                trading_symbol=active_trade.trading_symbol,
                exchange=active_trade.exchange,
                quantity=active_trade.quantity,
                stop_loss_order_id=active_trade.stop_loss_order_id,
                current_stop_loss_price=active_trade.stop_loss,
                new_stop_loss_price=new_stop_loss,
            )
        except Exception as exc:
            logging.exception("Failed to trail stop for %s", active_trade.trading_symbol)
            _print_order_error(f"Stop-loss modify failed for {active_trade.trading_symbol}: {exc}")
            return trade_manager.update_active_trade(symbol=active_trade.symbol, highest_price=highest_price)

    updated = trade_manager.update_active_trade(
        symbol=active_trade.symbol,
        stop_loss=broker_stop_loss,
        highest_price=highest_price,
    )
    if updated is not None:
        _print_trail_update(updated, current_price)
    return updated


def _print_trade_started(active_trade: ActiveTrade) -> None:
    lines = [
        "[TRADE STARTED]",
        f"Bought {active_trade.trading_symbol} at {_fmt_rupee(active_trade.entry_price or active_trade.entry_high)}",
        f"Qty: {active_trade.quantity}",
        f"Initial SL: {_fmt_rupee(active_trade.stop_loss)}",
    ]
    print(colorize("\n".join(lines), GREEN, bold=True))


def _print_trade_waiting(active_trade: ActiveTrade, current_price: float) -> None:
    lines = [
        "[WAITING FOR ENTRY]",
        f"{_contract_label(active_trade)} is inside watch mode.",
        f"Entry Range: {_fmt_rupee(active_trade.entry_low)} to {_fmt_rupee(active_trade.entry_high)}",
        f"Current LTP: {_fmt_rupee(current_price)} | Planned SL: {_fmt_rupee(active_trade.initial_stop_loss)}",
    ]
    print(colorize("\n".join(lines), CYAN, bold=True))


def _print_trail_update(active_trade: ActiveTrade, current_price: float) -> None:
    lines = [
        "[TRAIL SL UPDATED]",
        f"Price moved to {_fmt_rupee(current_price)}",
        f"New SL: {_fmt_rupee(active_trade.stop_loss)}",
    ]
    print(colorize("\n".join(lines), YELLOW, bold=True))


def _print_trade_running(active_trade: ActiveTrade, current_price: float) -> None:
    pnl_text = _format_pnl_pct(active_trade.entry_price, current_price)
    lines = [
        "[TRADE RUNNING]",
        f"LTP: {_fmt_rupee(current_price)} | SL: {_fmt_rupee(active_trade.stop_loss)}",
    ]
    print(colorize("\n".join(lines), CYAN, bold=True))


def _print_stop_loss_hit(active_trade: ActiveTrade, exit_price: float) -> None:
    pnl_text = _format_pnl_pct(active_trade.entry_price, exit_price)
    lines = [
        "[STOP LOSS HIT]",
        f"Exited at {_fmt_rupee(exit_price)}",
        f"PnL: {pnl_text}",
    ]
    print(colorize("\n".join(lines), RED, bold=True))


def _print_order_error(message: str) -> None:
    print(colorize(f"[ERROR] {message}", RED, bold=True))


def _print_signal(generated_signal) -> None:
    color = GREEN if generated_signal.signal in {"BUY_CE", "BUY"} else RED
    if generated_signal.details is not None and generated_signal.details.option_suggestion is not None:
        option = generated_signal.details.option_suggestion
        lines = [
            f"[SIGNAL] {generated_signal.symbol} | {_friendly_signal(generated_signal.signal)}",
            f"Confidence: {_format_confidence(generated_signal.confidence)}",
            f"Why: {generated_signal.details.summary}",
        ]
        if option.trading_symbol:
            lines.append(f"Contract: {option.trading_symbol}")
        if option.premium_ltp is not None:
            lines.append(f"Premium LTP: {_fmt_rupee(option.premium_ltp)}")
        if option.entry_low is not None and option.entry_high is not None:
            lines.append(f"Entry Range: {_fmt_rupee(option.entry_low)} to {_fmt_rupee(option.entry_high)}")
        if option.stop_loss is not None:
            lines.append(f"Stop Loss: {_fmt_rupee(option.stop_loss)}")
        print(colorize("\n".join(lines), color, bold=True))
        return

    summary = f"[SIGNAL] {generated_signal.symbol} | Action: {_friendly_signal(generated_signal.signal)} | Confidence: {_format_confidence(generated_signal.confidence)}"
    print(colorize(summary, color, bold=True))
    print(colorize(f"[WHY] {_humanize_reason(generated_signal.reason)}", color))


def _log_no_trade(symbol: str, reason: str) -> None:
    details = _parse_reason_details(reason)
    logging.info(
        "[NO TRADE] %s | Market bias=%s | Entry trigger=%s | Why=%s%s",
        symbol,
        details["market_bias"],
        details["entry_trigger"],
        details["why"],
        details["levels"],
    )


def _friendly_signal(signal: str) -> str:
    mapping = {
        "BUY_CE": "Bullish option buy (CE)",
        "BUY_PE": "Bearish option buy (PE)",
        "BUY": "Buy",
        "SELL": "Sell",
        "NO_TRADE": "No trade",
    }
    return mapping.get(signal, signal.replace("_", " ").title())


def _format_confidence(confidence: float) -> str:
    pct = int(round(max(0.0, min(confidence, 1.0)) * 100))
    if pct >= 85:
        label = "Very strong"
    elif pct >= 70:
        label = "Strong"
    elif pct >= 55:
        label = "Moderate"
    elif pct > 0:
        label = "Weak"
    else:
        label = "No edge"
    return f"{pct}% ({label})"


def _humanize_reason(reason: str) -> str:
    parts = [part for part in reason.split() if part]
    readable: list[str] = []

    for part in parts:
        if part == "ema_bullish":
            readable.append("EMA trend is bullish")
        elif part == "ema_bearish":
            readable.append("EMA trend is bearish")
        elif part == "rsi_supportive":
            readable.append("RSI supports the move")
        elif part == "price_breakout":
            readable.append("Price broke above the previous candle")
        elif part == "price_breakdown":
            readable.append("Price broke below the previous candle")
        elif part == "ema_trend_up":
            readable.append("EMA trend is up")
        elif part == "ema_trend_down":
            readable.append("EMA trend is down")
        elif part == "commodity_breakout":
            readable.append("Price closed above the recent breakout zone")
        elif part == "commodity_breakdown":
            readable.append("Price closed below the recent breakdown zone")
        elif part == "technical_filter_not_met":
            readable.append("Technical entry conditions were not met")
        elif part == "commodity_filter_not_met":
            readable.append("Commodity breakout conditions were not met")
        elif part == "indicator_warmup_pending":
            readable.append("Indicators are still warming up")
        elif part == "insufficient_closed_candles":
            readable.append("Not enough closed candles yet")
        elif part.startswith("ema9="):
            readable.append(f"EMA 9: {part.split('=', 1)[1]}")
        elif part.startswith("ema21="):
            readable.append(f"EMA 21: {part.split('=', 1)[1]}")
        elif part.startswith("rsi="):
            readable.append(f"RSI: {part.split('=', 1)[1]}")
        elif part.startswith("trend="):
            readable.append(f"Trend: {part.split('=', 1)[1].title()}")
        elif part.startswith("breakout="):
            readable.append(f"Breakout level: {part.split('=', 1)[1]}")
        elif part.startswith("breakdown="):
            readable.append(f"Breakdown level: {part.split('=', 1)[1]}")
        else:
            readable.append(part.replace("_", " "))

    return " | ".join(readable)


def _parse_reason_details(reason: str) -> dict[str, str]:
    parts = [part for part in reason.split() if part]

    trend_value = next(
        (part.split("=", 1)[1].strip().lower() for part in parts if part.startswith("trend=")),
        "neutral",
    )
    market_bias_map = {
        "bullish": "Bullish",
        "bearish": "Bearish",
        "neutral": "Neutral",
    }
    market_bias = market_bias_map.get(trend_value, trend_value.title())

    if "commodity_filter_not_met" in parts:
        entry_trigger = "Not confirmed"
        why = "Breakout or breakdown entry conditions were not met"
    elif "technical_filter_not_met" in parts:
        entry_trigger = "Not confirmed"
        why = "Technical entry conditions were not met"
    elif "indicator_warmup_pending" in parts:
        entry_trigger = "Waiting"
        why = "Indicators are still warming up"
    elif "insufficient_closed_candles" in parts:
        entry_trigger = "Waiting"
        why = "Not enough closed candles yet"
    else:
        entry_trigger = "Not confirmed"
        why = _humanize_reason(reason)

    levels_parts: list[str] = []
    for part in parts:
        if part.startswith("ema9="):
            levels_parts.append(f"EMA9={part.split('=', 1)[1]}")
        elif part.startswith("ema21="):
            levels_parts.append(f"EMA21={part.split('=', 1)[1]}")
        elif part.startswith("rsi="):
            levels_parts.append(f"RSI={part.split('=', 1)[1]}")
        elif part.startswith("breakout="):
            levels_parts.append(f"Breakout={part.split('=', 1)[1]}")
        elif part.startswith("breakdown="):
            levels_parts.append(f"Breakdown={part.split('=', 1)[1]}")

    levels = f" | Levels: {', '.join(levels_parts)}" if levels_parts else ""
    return {
        "market_bias": market_bias,
        "entry_trigger": entry_trigger,
        "why": why,
        "levels": levels,
    }


def _default_sentiment() -> dict[str, object]:
    return {
        "sentiment": "SIDEWAYS",
        "confidence": 0.0,
        "reason": "sentiment_disabled",
    }


def _extract_order_price(order: dict[str, object], fallback_price: float) -> float:
    average_price = order.get("average_price") or order.get("price") or fallback_price
    return float(average_price)


def _contract_label(active_trade: ActiveTrade) -> str:
    return f"{active_trade.symbol} {active_trade.option_type}".strip()


def _fmt_rupee(value: float) -> str:
    return f"Rs {value:.2f}"


def _format_pnl_pct(entry_price: float | None, current_price: float) -> str:
    if entry_price is None or entry_price <= 0:
        return "NA"
    pnl_pct = ((current_price - entry_price) / entry_price) * 100
    return f"{pnl_pct:+.2f}%"


def _reset_daily_state_if_needed() -> None:
    today = date.today().isoformat()
    if DAILY_STATE["date"] == today:
        return
    DAILY_STATE["date"] = today
    DAILY_STATE["daily_pnl"] = 0.0
    DAILY_STATE["completed_trades"] = 0
    DAILY_STATE["cooldown_candles"] = 0


def _is_trade_window_open(symbol: str) -> bool:
    now = datetime.now().time()
    market_type = os.getenv("MARKET_TYPE", "EQUITY").strip().upper()

    if market_type == "MCX":
        morning_session = dt_time(9, 0) <= now <= dt_time(17, 0)
        evening_session = dt_time(17, 1) <= now <= dt_time(23, 30)
        return morning_session or evening_session

    return dt_time(9, 20) <= now <= dt_time(14, 45)


def _daily_loss_limit_reached() -> bool:
    return float(DAILY_STATE["daily_pnl"]) <= (-1 * _env_float("MAX_DAILY_LOSS", 2000.0))


def _max_trades_reached() -> bool:
    return int(DAILY_STATE["completed_trades"]) >= int(_env_int("MAX_TRADES_PER_DAY", 5))


def _cooldown_active() -> bool:
    return int(DAILY_STATE["cooldown_candles"]) > 0


def _should_skip_trade(symbol: str, generated_signal, premium_price: float, candle_manager: CandleManager) -> bool:
    if not _premium_in_range(premium_price):
        _print_skip("Premium outside allowed range")
        return True
    if _ema_spread_too_small(generated_signal):
        _print_skip("Sideways market")
        return True
    if _recent_range_too_tight(symbol, candle_manager):
        _print_skip("Low volatility")
        return True
    return False


def _premium_in_range(premium_price: float) -> bool:
    return _env_float("MIN_PREMIUM", 80.0) <= premium_price <= _env_float("MAX_PREMIUM", 300.0)


def _ema_spread_too_small(generated_signal) -> bool:
    details = getattr(generated_signal, "details", None)
    indicator = getattr(details, "indicator_details", None)
    ema_9 = getattr(indicator, "ema_9", None)
    ema_21 = getattr(indicator, "ema_21", None)
    if ema_9 is None or ema_21 is None:
        return False
    return abs(float(ema_9) - float(ema_21)) < _env_float("EMA_SPREAD_THRESHOLD", 5.0)


def _recent_range_too_tight(symbol: str, candle_manager: CandleManager) -> bool:
    candles = candle_manager.get_closed_candles(symbol)[-5:]
    if len(candles) < 5:
        return False
    highest_high = max(float(candle.high) for candle in candles)
    lowest_low = min(float(candle.low) for candle in candles)
    return (highest_high - lowest_low) < _env_float("SIDEWAYS_RANGE_THRESHOLD", 20.0)


def _record_trade_result(active_trade: ActiveTrade, exit_price: float) -> None:
    entry_price = active_trade.entry_price or 0.0
    pnl_amount = (exit_price - entry_price) * active_trade.quantity
    DAILY_STATE["daily_pnl"] = float(DAILY_STATE["daily_pnl"]) + pnl_amount
    DAILY_STATE["completed_trades"] = int(DAILY_STATE["completed_trades"]) + 1
    if pnl_amount < 0:
        DAILY_STATE["cooldown_candles"] = 2


def _risk_based_capital(order_manager: OrderManager, base_capital: float, entry_price: float, stop_loss: float) -> float:
    risk_per_trade = max(base_capital * 0.01, 1.0)
    per_unit_risk = max(entry_price - stop_loss, 0.01)
    raw_quantity = max(int(risk_per_trade / per_unit_risk), 1)
    lot_size = order_manager.instrument.lot_size if order_manager.instrument is not None else 1
    if lot_size > 1:
        risk_quantity = max((raw_quantity // lot_size), 1) * lot_size
    else:
        risk_quantity = raw_quantity
    return round(risk_quantity * entry_price, 2)


def _print_skip(message: str) -> None:
    print(colorize(f"[SKIPPED] {message}", YELLOW, bold=True))


def _print_blocked(message: str) -> None:
    print(colorize(f"[BLOCKED] {message}", RED, bold=True))


def _env_float(name: str, default: float) -> float:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        return float(raw_value)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        return int(raw_value)
    except ValueError:
        return default


if __name__ == "__main__":
    main()



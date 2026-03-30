from __future__ import annotations

import logging
import os
from datetime import date, datetime, time as dt_time, timedelta

from utils_console import CYAN, GREEN, RED, YELLOW, colorize
from config import get_mode
from config.settings import Settings, get_settings
from data.candle_manager import CandleManager
from data.data_loader import HistoricalDataLoader, MINIMUM_STARTUP_CANDLES
from data.database import TradingDatabase
from data.market_data import MarketDataService
from data.option_premium import OptionPremiumService
from execution.order_manager import OrderManager
from execution.trade_manager import ActiveTrade, TradeManager
from strategy.equity_decision_engine import enrich_signal_with_premium
from strategy.indicators import calculate_ema
from strategy.market_regime import MarketRegimeSnapshot, detect_market_regime
from strategy.signal_engine import store_market_data, store_signal
from strategy.strategy import LastClosedCandleStrategy, MINIMUM_INDICATOR_CANDLES


# NEW: daily runtime controls and risk throttles
DAILY_STATE = {
    "date": None,
    "daily_pnl": 0.0,
    "peak_pnl": 0.0,
    "completed_trades": 0,
    "cooldown_candles": 0,
    "consecutive_losses": 0,
    "halted_for_day": False,
    "last_trade_pnl": 0.0,
}
RUNTIME_SETTINGS: Settings | None = None
RUNTIME_DATABASE: TradingDatabase | None = None


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    logging.getLogger("data.market_data").setLevel(logging.INFO)


def main() -> None:
    configure_logging()
    global RUNTIME_SETTINGS, RUNTIME_DATABASE
    mode = get_mode()
    settings = get_settings()
    RUNTIME_SETTINGS = settings
    symbol = settings.symbol
    market_type = settings.market_type
    instrument = settings.instruments[0]

    logging.info("Runtime MODE=%s", mode)
    logging.info("Runtime MARKET_TYPE=%s", market_type)
    logging.info("Runtime EXECUTION_PROFILE=%s", settings.execution_profile)
    logging.info("[INFO] Processing SYMBOL: %s", symbol)
    logging.info("[INFO] Sentiment and news integrations disabled for fast execution.")

    market_data = MarketDataService(settings=settings)
    database = TradingDatabase()
    RUNTIME_DATABASE = database
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

        _manage_active_trade(symbol, trade_manager, premium_service, order_manager, candle_manager)

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
    _print_mode_banner(mode, symbol, market_type)
    print(colorize(f"[{_mode_label()} WAIT] Processing SYMBOL: {symbol} | waiting for live ticks and the next {settings.candle_interval_minutes}-minute candle close...", YELLOW, bold=True))

    market_data.start()


def _handle_generated_signal(symbol: str, generated_signal, premium_service, spot_price: float, trade_manager: TradeManager, order_manager: OrderManager, candle_manager: CandleManager) -> None:
    _reset_daily_state_if_needed()
    settings = _execution_settings()
    if trade_manager.has_active_trade(symbol):
        print(colorize("[SKIPPED] Active trade already exists", YELLOW, bold=True))
        return
    if settings.kill_switch or DAILY_STATE["halted_for_day"]:
        _print_blocked("Kill switch active")
        return
    if not _is_trade_window_open(symbol):
        _print_skip("Time filter")
        return
    if _daily_loss_limit_reached():
        _print_blocked("Daily loss limit reached")
        return
    if _drawdown_limit_reached():
        _print_blocked("Max drawdown reached")
        return
    if _max_trades_reached():
        _print_blocked("Max trades reached")
        return
    if _cooldown_active():
        DAILY_STATE["cooldown_candles"] = max(int(DAILY_STATE["cooldown_candles"]) - 1, 0)
        _print_skip("Trade cooldown active")
        return

    regime_snapshot = detect_market_regime(
        candle_manager.get_closed_candles(symbol),
        adx_trending_threshold=settings.adx_trending_threshold,
        adx_sideways_threshold=settings.adx_sideways_threshold,
        atr_spike_multiplier=settings.atr_spike_multiplier,
        range_compression_threshold_pct=settings.range_compression_threshold_pct,
    )

    if generated_signal.signal == "BUY_CE":
        premium = _safe_premium_quote(premium_service, symbol, spot_price, "CALL")
        if premium is None:
            _print_premium_unavailable(symbol, "CE")
            return
        enriched_signal = enrich_signal_with_premium(generated_signal, premium)
        if _should_skip_trade(symbol, enriched_signal, premium.last_price, candle_manager, regime_snapshot):
            return
        _register_trade_plan(symbol, enriched_signal, premium, trade_manager, candle_manager, regime_snapshot)
        _print_signal(enriched_signal)
    elif generated_signal.signal == "BUY_PE":
        premium = _safe_premium_quote(premium_service, symbol, spot_price, "PUT")
        if premium is None:
            _print_premium_unavailable(symbol, "PE")
            return
        enriched_signal = enrich_signal_with_premium(generated_signal, premium)
        if _should_skip_trade(symbol, enriched_signal, premium.last_price, candle_manager, regime_snapshot):
            return
        _register_trade_plan(symbol, enriched_signal, premium, trade_manager, candle_manager, regime_snapshot)
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


def _register_trade_plan(
    symbol: str,
    generated_signal,
    premium,
    trade_manager: TradeManager,
    candle_manager: CandleManager,
    regime_snapshot: MarketRegimeSnapshot,
) -> ActiveTrade | None:
    if generated_signal.details is None or generated_signal.details.option_suggestion is None:
        return None
    option = generated_signal.details.option_suggestion
    if option.entry_low is None or option.entry_high is None or option.stop_loss is None:
        return None
    if trade_manager.has_active_trade(symbol):
        logging.info("Skipping signal for %s because one active trade already exists.", symbol)
        return None
    planned_stop_loss = _planned_stop_loss(
        generated_signal.signal,
        premium.last_price,
        generated_signal.stop_loss if generated_signal.stop_loss is not None else option.stop_loss,
        candle_manager.get_closed_candles(symbol),
        regime_snapshot,
    )
    signal_entry_price = generated_signal.entry_price if generated_signal.entry_price is not None else option.entry_high
    signal_target_price = generated_signal.target if generated_signal.target is not None else option.target
    rr_ratio = _calculate_rr(
        signal_entry_price,
        planned_stop_loss,
        signal_target_price if signal_target_price is not None else signal_entry_price + abs(signal_entry_price - planned_stop_loss),
    )
    last_candle = candle_manager.get_last_completed_candle(symbol)
    return trade_manager.open_trade_plan(
        symbol=symbol,
        signal=generated_signal.signal,
        trading_symbol=premium.trading_symbol,
        exchange=premium.exchange,
        option_type=premium.option_type or option.option_type,
        entry_low=option.entry_low,
        entry_high=option.entry_high,
        stop_loss=planned_stop_loss,
        entry_price=None,
        regime=regime_snapshot.regime,
        entry_reason=generated_signal.reason,
        rr_ratio=rr_ratio,
        target_price=signal_target_price,
        confirmation_high=(last_candle.high if last_candle is not None else None),
        confirmation_low=(last_candle.low if last_candle is not None else None),
    )


def _manage_active_trade(symbol: str, trade_manager: TradeManager, premium_service, order_manager: OrderManager, candle_manager: CandleManager) -> bool:
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
    if active_trade.status == "OPEN":
        active_trade = _refresh_trade_extremes(active_trade, current_price, trade_manager) or active_trade

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

        if not _entry_confirmation_passed(active_trade, candle_manager):
            _print_trade_waiting(active_trade, current_price)
            return True

        if active_trade.entry_low <= current_price <= active_trade.entry_high:
            updated_trade = _try_execute_entry_if_needed(symbol, active_trade, current_price, trade_manager, order_manager)
            if updated_trade is not None and updated_trade.status == "OPEN":
                _print_trade_running(updated_trade, current_price)
            return True

        _print_trade_waiting(active_trade, current_price)
        return True

    if _handle_live_stop_loss_completion(symbol, active_trade, current_price, trade_manager, order_manager):
        return True

    latest_candle = candle_manager.get_last_completed_candle(symbol)
    partial_trade = _handle_partial_profit(symbol, active_trade, current_price, trade_manager, order_manager)
    active_trade = partial_trade or trade_manager.get_active_trade(symbol) or active_trade

    if _should_time_exit(active_trade, current_price):
        exit_order = _safe_exit_position(active_trade, current_price, order_manager, quantity=active_trade.remaining_quantity or active_trade.quantity, reason="time_exit")
        if exit_order is None:
            return True
        exit_price = _extract_order_price(exit_order, current_price)
        closed_trade = trade_manager.close_active_trade(symbol, "time_exit", exit_price)
        if closed_trade is not None:
            _record_trade_result(closed_trade, exit_price)
            _print_time_exit(closed_trade, exit_price)
        return True

    if current_price <= active_trade.stop_loss:
        exit_order = _safe_exit_position(active_trade, current_price, order_manager, quantity=active_trade.remaining_quantity or active_trade.quantity, reason="stop_loss_hit")
        if exit_order is None:
            return True
        exit_price = _extract_order_price(exit_order, current_price)
        closed_trade = trade_manager.close_active_trade(symbol, "stop_loss_hit", exit_price)
        if closed_trade is not None:
            _record_trade_result(closed_trade, exit_price)
            _print_stop_loss_hit(closed_trade, exit_price)
        return True

    updated_trade = _trail_active_trade_if_needed(active_trade, current_price, trade_manager, order_manager, latest_candle)
    _print_trade_running(updated_trade or active_trade, current_price)
    return True


def _try_execute_entry_if_needed(symbol: str, active_trade: ActiveTrade, current_price: float, trade_manager: TradeManager, order_manager: OrderManager) -> ActiveTrade | None:
    latest_trade = trade_manager.get_active_trade(symbol) or active_trade
    if latest_trade.order_placed or latest_trade.status != "PENDING_ENTRY":
        return latest_trade
    if not (latest_trade.entry_low <= current_price <= latest_trade.entry_high):
        return latest_trade

    try:
        execution_price = _adjusted_entry_price(latest_trade, current_price)
        quantity = _compute_entry_quantity(order_manager, latest_trade, execution_price)
        managed_order = order_manager.place_market_buy(
            trading_symbol=latest_trade.trading_symbol,
            exchange=latest_trade.exchange,
            last_price=execution_price,
            stop_loss_price=latest_trade.stop_loss,
            quantity_override=quantity,
        )
        updated_trade = trade_manager.update_active_trade(
            symbol=symbol,
            status="OPEN",
            entry_price=managed_order.entry_price,
            stop_loss=managed_order.stop_loss_price,
            initial_stop_loss=latest_trade.initial_stop_loss,
            quantity=managed_order.quantity,
            remaining_quantity=managed_order.quantity,
            highest_price=managed_order.entry_price,
            mfe_price=managed_order.entry_price,
            mae_price=managed_order.entry_price,
            order_placed=True,
            entry_order_id=managed_order.entry_order_id,
            stop_loss_order_id=managed_order.stop_loss_order_id,
            opened_at=datetime.now().isoformat(),
        )
        if updated_trade is not None:
            _print_trade_started(updated_trade)
        return updated_trade
    except Exception as exc:
        logging.exception("Entry order failed for %s", latest_trade.trading_symbol)
        _print_order_error(f"Order failed for {latest_trade.trading_symbol}: {exc}")
        trade_manager.close_active_trade(symbol, "entry_order_failed", current_price)
        return None


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
            quantity=active_trade.remaining_quantity or active_trade.quantity,
            price=exit_price,
            status="STOP_LOSS_FILLED",
            reason="stop_loss_hit",
            order_id=active_trade.stop_loss_order_id,
        )
        closed_trade = trade_manager.close_active_trade(symbol, "stop_loss_hit", exit_price)
        if closed_trade is not None:
            _record_trade_result(closed_trade, exit_price)
            _print_stop_loss_hit(closed_trade, exit_price)
        return True

    if stop_status in {"REJECTED", "CANCELLED"} and current_price <= active_trade.stop_loss:
        _print_order_error(f"Stop-loss order {stop_status.lower()} for {active_trade.trading_symbol}. Exiting at market.")
        exit_order = _safe_exit_position(active_trade, current_price, order_manager, quantity=active_trade.remaining_quantity or active_trade.quantity, reason="emergency_stop_loss_exit")
        if exit_order is None:
            return True
        exit_price = _extract_order_price(exit_order, current_price)
        closed_trade = trade_manager.close_active_trade(symbol, "emergency_stop_loss_exit", exit_price)
        if closed_trade is not None:
            _record_trade_result(closed_trade, exit_price)
            _print_stop_loss_hit(closed_trade, exit_price)
        return True

    return False


def _safe_exit_position(
    active_trade: ActiveTrade,
    current_price: float,
    order_manager: OrderManager,
    quantity: int | None = None,
    reason: str = "manual_exit",
):
    try:
        exit_quantity = quantity or active_trade.remaining_quantity or active_trade.quantity
        if order_manager.mode == "LIVE" and active_trade.stop_loss_order_id:
            try:
                order_manager.cancel_order(active_trade.stop_loss_order_id)
            except Exception as exc:
                logging.warning("Unable to cancel stop-loss order for %s before exit: %s", active_trade.trading_symbol, exc)
        return order_manager.exit_position(
            trading_symbol=active_trade.trading_symbol,
            exchange=active_trade.exchange,
            quantity=exit_quantity,
            last_price=current_price,
            reason=reason,
        )
    except Exception as exc:
        logging.exception("Exit order failed for %s", active_trade.trading_symbol)
        _print_order_error(f"Exit failed for {active_trade.trading_symbol}: {exc}")
        return None


def _trail_active_trade_if_needed(active_trade: ActiveTrade, current_price: float, trade_manager: TradeManager, order_manager: OrderManager, latest_candle=None) -> ActiveTrade | None:
    entry_price = active_trade.entry_price
    if entry_price is None or active_trade.quantity <= 0:
        return active_trade

    settings = _execution_settings()
    highest_price = max(active_trade.highest_price or entry_price, current_price)
    initial_risk = max(entry_price - active_trade.initial_stop_loss, 0.01)
    reward_multiple = max((highest_price - entry_price) / initial_risk, 0.0)

    locked_stop = active_trade.stop_loss
    if reward_multiple >= 1.0:
        locked_stop = max(locked_stop, entry_price)
    if reward_multiple >= 1.5:
        locked_stop = max(locked_stop, entry_price + (initial_risk * 0.5))
    if reward_multiple >= 2.0:
        locked_stop = max(locked_stop, entry_price + initial_risk)

    if settings.trailing_mode == "FIXED_STEP":
        trailing_candidate = highest_price * (1.0 - settings.fixed_trail_step_pct)
    else:
        trailing_buffer = max(highest_price * settings.trailing_buffer_pct, initial_risk * settings.trailing_rr_lock_step)
        trailing_candidate = highest_price - trailing_buffer

    new_stop_loss = max(active_trade.stop_loss, locked_stop, trailing_candidate)
    new_stop_loss = min(new_stop_loss, current_price * 0.995)

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
                quantity=active_trade.remaining_quantity or active_trade.quantity,
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
        f"[{_mode_label()} TRADE STARTED]",
        f"Bought {active_trade.trading_symbol} at {_fmt_rupee(active_trade.entry_price or active_trade.entry_high)}",
        f"Qty: {active_trade.quantity}",
        f"Initial SL: {_fmt_rupee(active_trade.stop_loss)}",
        f"Target: {_fmt_rupee(active_trade.target_price)}" if active_trade.target_price is not None else "Target: Open",
        f"Regime: {active_trade.regime} | RR: {active_trade.rr_ratio:.2f}",
    ]
    print(colorize("\n".join(lines), _mode_color(), bold=True))

def _print_trade_waiting(active_trade: ActiveTrade, current_price: float) -> None:
    lines = [
        f"[{_mode_label()} WAITING FOR ENTRY]",
        f"{_contract_label(active_trade)} is inside watch mode.",
        f"Entry Range: {_fmt_rupee(active_trade.entry_low)} to {_fmt_rupee(active_trade.entry_high)}",
        f"Current LTP: {_fmt_rupee(current_price)} | Planned SL: {_fmt_rupee(active_trade.initial_stop_loss)}",
    ]
    print(colorize("\n".join(lines), _mode_color(), bold=True))


def _print_trail_update(active_trade: ActiveTrade, current_price: float) -> None:
    lines = [
        f"[{_mode_label()} TRAIL SL UPDATED]",
        f"Price moved to {_fmt_rupee(current_price)}",
        f"New SL: {_fmt_rupee(active_trade.stop_loss)}",
    ]
    print(colorize("\n".join(lines), _mode_color(), bold=True))


def _print_trade_running(active_trade: ActiveTrade, current_price: float) -> None:
    pnl_text = _format_pnl_pct(active_trade.entry_price, current_price)
    lines = [
        f"[{_mode_label()} TRADE RUNNING]",
        f"LTP: {_fmt_rupee(current_price)} | SL: {_fmt_rupee(active_trade.stop_loss)} | PnL: {pnl_text}",
    ]
    if active_trade.target_price is not None:
        lines.append(f"Target: {_fmt_rupee(active_trade.target_price)} | Realized: {_fmt_rupee(active_trade.realized_pnl)}")
    print(colorize("\n".join(lines), _mode_color(), bold=True))

def _print_stop_loss_hit(active_trade: ActiveTrade, exit_price: float) -> None:
    pnl_text = _format_pnl_pct(active_trade.entry_price, exit_price)
    lines = [
        f"[{_mode_label()} STOP LOSS HIT]",
        f"Exited at {_fmt_rupee(exit_price)}",
        f"PnL: {pnl_text}",
    ]
    print(colorize("\n".join(lines), _mode_color(), bold=True))


def _print_order_error(message: str) -> None:
    print(colorize(f"[{_mode_label()} ERROR] {message}", RED, bold=True))


def _print_signal(generated_signal) -> None:
    color = _mode_color()
    if generated_signal.details is not None and generated_signal.details.option_suggestion is not None:
        option = generated_signal.details.option_suggestion
        lines = [
            f"[{_mode_label()} SIGNAL] {generated_signal.symbol} | {_friendly_signal(generated_signal.signal)}",
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

    summary = f"[{_mode_label()} SIGNAL] {generated_signal.symbol} | Action: {_friendly_signal(generated_signal.signal)} | Confidence: {_format_confidence(generated_signal.confidence)}"
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


def _refresh_trade_extremes(active_trade: ActiveTrade, current_price: float, trade_manager: TradeManager) -> ActiveTrade | None:
    entry_anchor = active_trade.entry_price or current_price
    mfe_price = max(active_trade.mfe_price or entry_anchor, current_price)
    mae_price = min(active_trade.mae_price or entry_anchor, current_price)
    return trade_manager.update_active_trade(
        symbol=active_trade.symbol,
        mfe_price=mfe_price,
        mae_price=mae_price,
    )


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
    DAILY_STATE["peak_pnl"] = 0.0
    DAILY_STATE["completed_trades"] = 0
    DAILY_STATE["cooldown_candles"] = 0
    DAILY_STATE["consecutive_losses"] = 0
    DAILY_STATE["halted_for_day"] = False
    DAILY_STATE["last_trade_pnl"] = 0.0


def _is_trade_window_open(symbol: str) -> bool:
    settings = _execution_settings()
    now = datetime.now().time()
    market_type = os.getenv("MARKET_TYPE", "EQUITY").strip().upper()

    if market_type == "MCX":
        morning_session = dt_time(9, 0) <= now <= dt_time(17, 0)
        evening_session = dt_time(17, 1) <= now <= dt_time(23, 30)
        return morning_session or evening_session

    morning_open = _parse_time(settings.trading_window_morning_start)
    morning_close = _parse_time(settings.trading_window_morning_end)
    afternoon_open = _parse_time(settings.trading_window_afternoon_start)
    afternoon_close = _parse_time(settings.trading_window_afternoon_end)
    return _time_in_range(now, morning_open, morning_close) or _time_in_range(now, afternoon_open, afternoon_close)


def _daily_loss_limit_reached() -> bool:
    settings = _execution_settings()
    if float(DAILY_STATE["daily_pnl"]) <= (-1 * settings.max_daily_loss):
        DAILY_STATE["halted_for_day"] = True
        return True
    return False


def _drawdown_limit_reached() -> bool:
    settings = _execution_settings()
    drawdown = float(DAILY_STATE["peak_pnl"]) - float(DAILY_STATE["daily_pnl"])
    if drawdown >= settings.max_drawdown:
        DAILY_STATE["halted_for_day"] = True
        return True
    return False


def _max_trades_reached() -> bool:
    return int(DAILY_STATE["completed_trades"]) >= _execution_settings().max_trades_per_day


def _cooldown_active() -> bool:
    return int(DAILY_STATE["cooldown_candles"]) > 0


def _should_skip_trade(symbol: str, generated_signal, premium_price: float, candle_manager: CandleManager, regime_snapshot: MarketRegimeSnapshot) -> bool:
    settings = _execution_settings()
    if not _premium_in_range(premium_price):
        _print_skip("Premium outside allowed range")
        return True

    score, reasons, threshold = _calculate_filter_score(
        symbol=symbol,
        generated_signal=generated_signal,
        premium_price=premium_price,
        candle_manager=candle_manager,
        regime_snapshot=regime_snapshot,
    )
    if score > threshold:
        _print_skip(f"High penalty score ({score:.2f}/{threshold:.2f}) | {'; '.join(reasons[:3])}")
        logging.info(
            "[FILTER SCORE] Trade rejected due to high penalty | %s | penalty_score=%.2f | threshold=%.2f | reasons=%s",
            symbol,
            score,
            threshold,
            reasons,
        )
        return True

    logging.info(
        "[FILTER SCORE] Trade accepted | %s | penalty_score=%.2f | threshold=%.2f | reasons=%s",
        symbol,
        score,
        threshold,
        reasons or ["clean_setup"],
    )
    return False


# NEW: adaptive scoring so filters reduce weak trades without killing frequency.
def _calculate_filter_score(
    symbol: str,
    generated_signal,
    premium_price: float,
    candle_manager: CandleManager,
    regime_snapshot: MarketRegimeSnapshot,
) -> tuple[float, list[str], float]:
    settings = _execution_settings()
    penalties: list[tuple[float, str]] = []

    regime_penalty = _regime_penalty(regime_snapshot, settings)
    if regime_penalty > 0:
        penalties.append((regime_penalty, f"regime={regime_snapshot.regime.lower()}"))

    ema_penalty = _ema_spread_penalty(generated_signal, settings)
    if ema_penalty > 0:
        penalties.append((ema_penalty, "ema_spread_tight"))

    volatility_penalty = _volatility_penalty(symbol, candle_manager, settings)
    if volatility_penalty > 0:
        penalties.append((volatility_penalty, "recent_range_compressed"))

    vwap_penalty, volume_penalty, market_reasons = _vwap_volume_penalties(
        symbol=symbol,
        signal=generated_signal.signal,
        candle_manager=candle_manager,
        regime_snapshot=regime_snapshot,
        settings=settings,
    )
    if vwap_penalty > 0:
        penalties.append((vwap_penalty, "vwap_misaligned"))
    if volume_penalty > 0:
        penalties.append((volume_penalty, "volume_below_confirmation"))
    for reason in market_reasons:
        penalties.append((0.0, reason))

    higher_tf_penalty = _higher_timeframe_penalty(symbol, generated_signal.signal, candle_manager, settings)
    if higher_tf_penalty > 0:
        penalties.append((higher_tf_penalty, "higher_tf_misaligned"))

    threshold = settings.filter_score_threshold
    if regime_snapshot.regime == "TRENDING":
        threshold += settings.filter_score_trending_bonus
    elif regime_snapshot.regime == "VOLATILE":
        threshold += settings.filter_score_volatile_bonus
    elif regime_snapshot.regime == "SIDEWAYS":
        threshold -= settings.filter_score_sideways_penalty

    confidence = max(0.0, min(float(getattr(generated_signal, "confidence", 0.0) or 0.0), 1.0))
    threshold += max(confidence - 0.55, 0.0) * settings.filter_confidence_bonus

    raw_score = sum(value for value, _ in penalties)
    score = round(min(raw_score, settings.max_filter_penalty_cap), 2)
    reasons = [reason for _, reason in penalties if reason]
    return score, reasons, round(max(threshold, 1.0), 2)


# NEW: optional higher timeframe trend gate without changing core signals
def _higher_timeframe_penalty(symbol: str, signal: str, candle_manager: CandleManager, settings) -> float:
    if not settings.enable_higher_timeframe_trend_filter:
        return 0.0
    candles = candle_manager.get_closed_candles(symbol)
    aggregated_closes = _aggregate_higher_timeframe_closes(candles, settings.higher_timeframe_multiple)
    if len(aggregated_closes) < settings.higher_timeframe_slow_ema:
        return 0.0

    fast_ema = calculate_ema(aggregated_closes, settings.higher_timeframe_fast_ema)
    slow_ema = calculate_ema(aggregated_closes, settings.higher_timeframe_slow_ema)
    if fast_ema is None or slow_ema is None:
        return 0.0

    ema_distance_pct = abs(fast_ema - slow_ema) / max(abs(slow_ema), 0.01)
    severity = min(max(0.25 + (ema_distance_pct / 0.003), 0.25), 1.0)
    if signal == "BUY_CE" and fast_ema <= slow_ema:
        return round(severity * settings.filter_higher_tf_weight, 2)
    if signal == "BUY_PE" and fast_ema >= slow_ema:
        return round(severity * settings.filter_higher_tf_weight, 2)
    return 0.0


def _regime_penalty(regime_snapshot: MarketRegimeSnapshot, settings) -> float:
    if regime_snapshot.regime == "SIDEWAYS":
        base_penalty = settings.filter_sideways_weight
        volume_ratio = regime_snapshot.volume_spike_ratio or 0.0
        if volume_ratio >= settings.volume_spike_multiplier:
            return round(base_penalty * 0.35, 2)
        return round(base_penalty, 2)
    return 0.0


def _premium_in_range(premium_price: float) -> bool:
    settings = _execution_settings()
    return settings.min_premium <= premium_price <= settings.max_premium


def _ema_spread_too_small(generated_signal) -> bool:
    return _ema_spread_penalty(generated_signal, _execution_settings()) > 0.75


def _ema_spread_penalty(generated_signal, settings) -> float:
    details = getattr(generated_signal, "details", None)
    indicator = getattr(details, "indicator_details", None)
    ema_9 = getattr(indicator, "ema_9", None)
    ema_21 = getattr(indicator, "ema_21", None)
    if ema_9 is None or ema_21 is None:
        return 0.0
    threshold = _env_float("EMA_SPREAD_THRESHOLD", 5.0)
    spread = abs(float(ema_9) - float(ema_21))
    if spread >= threshold:
        return 0.0
    shortfall_ratio = 1.0 - (spread / max(threshold, 0.01))
    return round(shortfall_ratio * settings.filter_ema_weight, 2)


def _recent_range_too_tight(symbol: str, candle_manager: CandleManager) -> bool:
    return _volatility_penalty(symbol, candle_manager, _execution_settings()) > 0.75


def _volatility_penalty(symbol: str, candle_manager: CandleManager, settings) -> float:
    candles = candle_manager.get_closed_candles(symbol)[-5:]
    if len(candles) < 5:
        return 0.0
    highest_high = max(float(candle.high) for candle in candles)
    lowest_low = min(float(candle.low) for candle in candles)
    threshold_pct = settings.range_compression_threshold_pct
    reference_price = max(float(candles[-1].close), 0.01)
    actual_range_pct = (highest_high - lowest_low) / reference_price
    if actual_range_pct >= threshold_pct:
        return 0.0
    shortfall_ratio = 1.0 - (actual_range_pct / max(threshold_pct, 0.0001))
    return round(shortfall_ratio * settings.filter_volatility_weight, 2)


# IMPROVED: persist trade analytics and update live performance stats
def _record_trade_result(active_trade: ActiveTrade, exit_price: float) -> None:
    entry_price = active_trade.entry_price or 0.0
    exited_quantity = active_trade.remaining_quantity or active_trade.quantity
    running_leg_pnl = (exit_price - entry_price) * exited_quantity
    pnl_amount = float(active_trade.realized_pnl) + running_leg_pnl
    DAILY_STATE["daily_pnl"] = float(DAILY_STATE["daily_pnl"]) + running_leg_pnl
    DAILY_STATE["last_trade_pnl"] = pnl_amount
    DAILY_STATE["peak_pnl"] = max(float(DAILY_STATE["peak_pnl"]), float(DAILY_STATE["daily_pnl"]))
    DAILY_STATE["completed_trades"] = int(DAILY_STATE["completed_trades"]) + 1
    if pnl_amount < 0:
        DAILY_STATE["consecutive_losses"] = int(DAILY_STATE["consecutive_losses"]) + 1
        if DAILY_STATE["consecutive_losses"] >= _execution_settings().stop_after_three_losses:
            DAILY_STATE["halted_for_day"] = True
        elif DAILY_STATE["consecutive_losses"] >= 2:
            DAILY_STATE["cooldown_candles"] = _execution_settings().cooldown_after_two_losses
        else:
            DAILY_STATE["cooldown_candles"] = max(int(DAILY_STATE["cooldown_candles"]), 2)
    else:
        DAILY_STATE["consecutive_losses"] = 0

    if RUNTIME_DATABASE is not None and entry_price > 0:
        opened_at = active_trade.opened_at
        closed_at = datetime.now().isoformat()
        pnl_pct = (pnl_amount / max(entry_price * max(active_trade.quantity, 1), 0.01)) * 100.0
        mfe_pct = None if active_trade.mfe_price is None else ((active_trade.mfe_price - entry_price) / entry_price) * 100.0
        mae_pct = None if active_trade.mae_price is None else ((active_trade.mae_price - entry_price) / entry_price) * 100.0
        duration_minutes = None
        if opened_at is not None:
            try:
                duration_minutes = (datetime.fromisoformat(closed_at) - datetime.fromisoformat(opened_at)).total_seconds() / 60.0
            except ValueError:
                duration_minutes = None
        RUNTIME_DATABASE.store_trade_summary(
            symbol=active_trade.symbol,
            trading_symbol=active_trade.trading_symbol,
            signal=active_trade.signal,
            entry_price=entry_price,
            exit_price=exit_price,
            quantity=max(active_trade.quantity, 1),
            pnl=pnl_amount,
            pnl_pct=pnl_pct,
            exit_reason=active_trade.exit_reason or "closed",
            regime=active_trade.regime,
            entry_reason=active_trade.entry_reason,
            partial_exit_done=active_trade.partial_exit_done,
            realized_pnl=active_trade.realized_pnl,
            rr_ratio=active_trade.rr_ratio,
            target_price=active_trade.target_price,
            mfe_pct=mfe_pct,
            mae_pct=mae_pct,
            opened_at=opened_at,
            closed_at=closed_at,
            duration_minutes=duration_minutes,
        )
        metrics = RUNTIME_DATABASE.get_trade_performance(active_trade.symbol)
        logging.info(
            "[PERFORMANCE] %s | win_rate=%.2f%% | profit_factor=%.2f | avg_rr=%.2f | max_drawdown=%.2f | net_pnl=%.2f",
            active_trade.symbol,
            metrics["win_rate"],
            metrics["profit_factor"],
            metrics["avg_rr_proxy"],
            metrics["max_drawdown"],
            metrics["net_pnl"],
        )

def _risk_based_capital(order_manager: OrderManager, base_capital: float, entry_price: float, stop_loss: float) -> float:
    settings = _execution_settings()
    risk_per_trade = max(settings.account_balance * settings.risk_per_trade_pct, 1.0)
    per_unit_risk = max(abs(entry_price - stop_loss), 0.01)
    raw_quantity = max(int(risk_per_trade / per_unit_risk), 1)
    lot_size = order_manager.instrument.lot_size if order_manager.instrument is not None else 1
    if lot_size > 1:
        risk_quantity = max((raw_quantity // lot_size), 1) * lot_size
    else:
        risk_quantity = raw_quantity
    capital = min(risk_quantity * entry_price, settings.max_capital_exposure, base_capital)
    return round(capital, 2)

def _execution_settings():
    return (RUNTIME_SETTINGS or get_settings()).execution


def _adjusted_entry_price(active_trade: ActiveTrade, current_price: float) -> float:
    settings = _execution_settings()
    factor = settings.slippage_entry_factor_high if active_trade.regime == "VOLATILE" else settings.slippage_entry_factor
    return round(current_price * factor, 2)


def _compute_entry_quantity(order_manager: OrderManager, active_trade: ActiveTrade, execution_price: float) -> int:
    settings = _execution_settings()
    risk_qty = order_manager.calculate_risk_quantity(execution_price, active_trade.stop_loss)
    capital_cap = _risk_based_capital(order_manager, settings.capital_per_trade, execution_price, active_trade.stop_loss)
    if active_trade.regime == "VOLATILE":
        capital_cap = min(capital_cap, settings.capital_per_trade * 0.5)
    drawdown_scale = _drawdown_position_scale()
    capital_cap = max(capital_cap * drawdown_scale, execution_price)
    capital_qty = order_manager.calculate_quantity(execution_price, capital_override=capital_cap)
    base_quantity = min(risk_qty, capital_qty)
    lot_floor = order_manager.instrument.lot_size if order_manager.instrument is not None else 1
    return max(base_quantity, lot_floor)


def _drawdown_position_scale() -> float:
    settings = _execution_settings()
    if settings.max_daily_loss <= 0:
        return 1.0
    daily_loss_pressure = abs(min(float(DAILY_STATE["daily_pnl"]), 0.0)) / settings.max_daily_loss
    scale = 1.0
    if daily_loss_pressure >= settings.drawdown_size_reduction_start_pct:
        scale = min(scale, settings.drawdown_size_reduction_factor)
    if int(DAILY_STATE["consecutive_losses"]) >= 2:
        scale = min(scale, settings.drawdown_size_reduction_factor)
    return max(scale, 0.25)

def _passes_vwap_volume_filter(symbol: str, signal: str, candle_manager: CandleManager, regime_snapshot: MarketRegimeSnapshot) -> bool:
    vwap_penalty, volume_penalty, _ = _vwap_volume_penalties(
        symbol=symbol,
        signal=signal,
        candle_manager=candle_manager,
        regime_snapshot=regime_snapshot,
        settings=_execution_settings(),
    )
    return (vwap_penalty + volume_penalty) <= 0.75


def _vwap_volume_penalties(
    symbol: str,
    signal: str,
    candle_manager: CandleManager,
    regime_snapshot: MarketRegimeSnapshot,
    settings,
) -> tuple[float, float, list[str]]:
    candles = candle_manager.get_closed_candles(symbol)
    if not candles:
        return 0.0, 0.0, []

    reasons: list[str] = []
    last_close = float(candles[-1].close)
    vwap = regime_snapshot.vwap
    volume_spike_ratio = regime_snapshot.volume_spike_ratio
    vwap_penalty = 0.0
    volume_penalty = 0.0

    if settings.enable_vwap_filter and vwap is not None:
        distance = abs(last_close - vwap) / max(last_close, 0.01)
        if signal == "BUY_CE" and last_close <= vwap:
            severity = min(max(distance / 0.004, 0.2), 1.0)
            vwap_penalty = round(severity * settings.filter_vwap_weight, 2)
            reasons.append("price_below_vwap")
        elif signal == "BUY_PE" and last_close >= vwap:
            severity = min(max(distance / 0.004, 0.2), 1.0)
            vwap_penalty = round(severity * settings.filter_vwap_weight, 2)
            reasons.append("price_above_vwap")

    if settings.enable_volume_filter and volume_spike_ratio is not None and settings.volume_spike_multiplier > 0:
        if volume_spike_ratio < settings.volume_spike_multiplier:
            shortfall_ratio = 1.0 - (volume_spike_ratio / settings.volume_spike_multiplier)
            volume_penalty = round(shortfall_ratio * settings.filter_volume_weight, 2)
            reasons.append(f"volume_ratio={volume_spike_ratio:.2f}x")

    return vwap_penalty, volume_penalty, reasons

def _planned_stop_loss(signal: str, premium_price: float, suggested_stop: float, candles, regime_snapshot: MarketRegimeSnapshot) -> float:
    settings = _execution_settings()
    base_stop = suggested_stop if 0 < suggested_stop < premium_price else premium_price * (1 - settings.stop_loss_percent)

    normalized_distance_pct = None
    if candles:
        last_spot = max(float(candles[-1].close), 0.01)
        if settings.stop_loss_mode == "CANDLE":
            reference = candles[-1]
            if signal == "BUY_CE":
                normalized_distance_pct = (last_spot - float(reference.low)) / last_spot
            else:
                normalized_distance_pct = (float(reference.high) - last_spot) / last_spot
        elif regime_snapshot.atr is not None:
            normalized_distance_pct = float(regime_snapshot.atr) / last_spot

    if normalized_distance_pct is not None:
        distance_pct = min(
            max(normalized_distance_pct * settings.atr_stop_multiplier, settings.min_stop_loss_pct),
            settings.max_stop_loss_pct,
        )
        dynamic_stop = premium_price * (1 - distance_pct)
        return round(max(dynamic_stop, 0.05), 2)

    fallback_stop = premium_price * (1 - min(max(settings.stop_loss_percent, settings.min_stop_loss_pct), settings.max_stop_loss_pct))
    return round(max(min(base_stop, premium_price - 0.05), fallback_stop, 0.05), 2)

def _entry_confirmation_passed(active_trade: ActiveTrade, candle_manager: CandleManager) -> bool:
    last_candle = candle_manager.get_last_completed_candle(active_trade.symbol)
    if last_candle is None:
        return False
    if active_trade.signal == "BUY_CE" and active_trade.confirmation_high is not None:
        return float(last_candle.high) > float(active_trade.confirmation_high)
    if active_trade.signal == "BUY_PE" and active_trade.confirmation_low is not None:
        return float(last_candle.low) < float(active_trade.confirmation_low)
    return True


def _handle_partial_profit(symbol: str, active_trade: ActiveTrade, current_price: float, trade_manager: TradeManager, order_manager: OrderManager) -> ActiveTrade | None:
    if active_trade.partial_exit_done or active_trade.entry_price is None or active_trade.remaining_quantity <= 1:
        return active_trade

    initial_risk = max(active_trade.entry_price - active_trade.initial_stop_loss, 0.01)
    current_reward = current_price - active_trade.entry_price
    rr_now = current_reward / initial_risk
    if rr_now < _execution_settings().partial_profit_rr:
        return active_trade

    partial_qty_pct = min(max(_execution_settings().partial_profit_size_pct, 0.1), 0.9)
    partial_quantity = max(int(active_trade.remaining_quantity * partial_qty_pct), 1)
    if order_manager.instrument is not None and order_manager.instrument.lot_size > 1:
        partial_quantity = max((partial_quantity // order_manager.instrument.lot_size), 1) * order_manager.instrument.lot_size
        if partial_quantity >= active_trade.remaining_quantity:
            partial_quantity = max(active_trade.remaining_quantity - order_manager.instrument.lot_size, 0)
    if partial_quantity <= 0:
        return active_trade

    exit_order = _safe_exit_position(active_trade, current_price, order_manager, quantity=partial_quantity, reason="partial_profit")
    if exit_order is None:
        return active_trade
    exit_price = _extract_order_price(exit_order, current_price)
    remaining_quantity = max(active_trade.remaining_quantity - partial_quantity, 0)
    new_stop_order_id = active_trade.stop_loss_order_id
    breakeven_stop = max(active_trade.stop_loss, active_trade.entry_price)
    if remaining_quantity > 0:
        try:
            new_stop_order_id = order_manager.replace_stop_loss_order(
                trading_symbol=active_trade.trading_symbol,
                exchange=active_trade.exchange,
                quantity=remaining_quantity,
                current_order_id=active_trade.stop_loss_order_id,
                stop_loss_price=breakeven_stop,
            )
        except Exception as exc:
            logging.exception("Failed to replace stop-loss order after partial exit for %s", active_trade.trading_symbol)
            _print_order_error(f"Stop-loss replace failed for {active_trade.trading_symbol}: {exc}")
            return active_trade

    order_manager.trade_manager.record_trade(
        mode=order_manager.mode,
        symbol=active_trade.trading_symbol,
        side="SELL",
        quantity=partial_quantity,
        price=exit_price,
        status="PARTIAL_EXIT",
        reason="partial_profit",
    )
    realized_pnl = (exit_price - (active_trade.entry_price or exit_price)) * partial_quantity
    DAILY_STATE["daily_pnl"] = float(DAILY_STATE["daily_pnl"]) + realized_pnl
    DAILY_STATE["peak_pnl"] = max(float(DAILY_STATE["peak_pnl"]), float(DAILY_STATE["daily_pnl"]))
    updated_trade = trade_manager.update_active_trade(
        symbol=symbol,
        remaining_quantity=remaining_quantity,
        partial_exit_done=True,
        stop_loss=breakeven_stop,
        stop_loss_order_id=new_stop_order_id,
        realized_pnl=float(active_trade.realized_pnl) + realized_pnl,
    )
    if updated_trade is not None:
        _print_partial_exit(updated_trade, exit_price, partial_quantity)
    return updated_trade

def _should_time_exit(active_trade: ActiveTrade, current_price: float) -> bool:
    if active_trade.opened_at is None or active_trade.entry_price is None:
        return False
    settings = _execution_settings()
    now = datetime.now()
    try:
        opened_at = datetime.fromisoformat(active_trade.opened_at)
    except ValueError:
        return False
    close_time = _market_close_time()
    if now >= close_time - timedelta(minutes=settings.hard_flatten_minutes_before_close):
        return True
    if now < opened_at + timedelta(minutes=settings.time_exit_minutes):
        return False
    movement_pct = abs(current_price - active_trade.entry_price) / max(active_trade.entry_price, 0.01)
    return movement_pct <= settings.no_movement_threshold_pct


def _market_close_time() -> datetime:
    now = datetime.now()
    market_type = os.getenv("MARKET_TYPE", "EQUITY").strip().upper()
    close_hour, close_minute = (23, 30) if market_type == "MCX" else (15, 30)
    return now.replace(hour=close_hour, minute=close_minute, second=0, microsecond=0)

def _calculate_rr(entry_price: float, stop_loss: float, target_price: float) -> float:
    risk = max(entry_price - stop_loss, 0.01)
    reward = max(target_price - entry_price, 0.0)
    return round(reward / risk, 2)


def _parse_time(value: str) -> dt_time:
    return datetime.strptime(value.strip(), "%H:%M").time()


def _time_in_range(value: dt_time, start: dt_time, end: dt_time) -> bool:
    return start <= value <= end


def _print_partial_exit(active_trade: ActiveTrade, exit_price: float, quantity: int) -> None:
    lines = [
        f"[{_mode_label()} PARTIAL EXIT]",
        f"Booked {quantity} at {_fmt_rupee(exit_price)}",
        f"Remaining Qty: {active_trade.remaining_quantity} | New SL: {_fmt_rupee(active_trade.stop_loss)}",
    ]
    print(colorize("\n".join(lines), _mode_color(), bold=True))


def _print_time_exit(active_trade: ActiveTrade, exit_price: float) -> None:
    lines = [
        f"[{_mode_label()} TIME EXIT]",
        f"Exited {active_trade.trading_symbol} at {_fmt_rupee(exit_price)}",
        f"PnL: {_format_pnl_pct(active_trade.entry_price, exit_price)}",
    ]
    print(colorize("\n".join(lines), _mode_color(), bold=True))


def _print_skip(message: str) -> None:
    print(colorize(f"[{_mode_label()} SKIPPED] {message}", YELLOW, bold=True))


def _print_blocked(message: str) -> None:
    print(colorize(f"[{_mode_label()} BLOCKED] {message}", RED, bold=True))


def _print_mode_banner(mode: str, symbol: str, market_type: str) -> None:
    color = GREEN if mode == "LIVE" else CYAN
    lines = [
        f"[{mode} MODE ACTIVE]",
        f"Symbol: {symbol} | Market: {market_type}",
        "Real broker orders enabled." if mode == "LIVE" else "Paper simulation only. No real orders will be placed.",
    ]
    print(colorize("\n".join(lines), color, bold=True))


def _mode_label() -> str:
    return get_mode().upper()


def _mode_color() -> str:
    return GREEN if _mode_label() == "LIVE" else CYAN


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



from __future__ import annotations

import logging

from utils_console import GREEN, RED, YELLOW, colorize
from config import get_mode
from config.settings import get_settings
from data.candle_manager import CandleManager
from data.data_loader import HistoricalDataLoader, MINIMUM_STARTUP_CANDLES
from data.database import TradingDatabase
from data.market_data import MarketDataService
from data.news_service import NewsCacheService
from data.option_premium import OptionPremiumService
from execution.order_manager import OrderManager
from execution.trade_manager import ActiveTrade, TradeManager
from market_selector import get_market_profile
from strategy.signal_engine import fetch_or_get_cached_news, get_sentiment_with_cache, store_market_data, store_signal
from strategy.equity_decision_engine import enrich_signal_with_premium, format_output
from strategy.strategy import LastClosedCandleStrategy, MINIMUM_INDICATOR_CANDLES


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
    market_profile = get_market_profile(settings)

    logging.info("Runtime MODE=%s", mode)
    logging.info("Runtime MARKET_TYPE=%s", market_type)
    logging.info("[INFO] Processing SYMBOL: %s", symbol)

    market_data = MarketDataService(settings=settings)
    database = TradingDatabase()
    candle_manager = CandleManager(max_candles=100)
    loader = HistoricalDataLoader(market_data, database)
    news_service = NewsCacheService(database)
    premium_service = OptionPremiumService(market_data.clients.kite, market_type=market_type)
    trade_manager = TradeManager()
    order_manager = OrderManager(
        market_data.clients.kite,
        settings.execution,
        instrument=instrument,
        trade_manager=trade_manager,
    )
    _ = order_manager

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

    startup_sentiment = _get_market_sentiment(
        symbol=symbol,
        market_profile=market_profile,
        news_service=news_service,
    )

    generated_signal = strategy.evaluate(startup_sentiment)
    if generated_signal is not None:
        try:
            store_signal(generated_signal, database)
        except Exception as exc:
            logging.warning("Failed to store startup signal in DB: %s", exc)

        last_completed = candle_manager.get_last_completed_candle(symbol)
        if last_completed is not None:
            logging.info(
                "[INFO] Processing SYMBOL: %s | Startup analysis ready | last_completed=%s | sentiment=%s | signal=%s",
                symbol,
                last_completed.end.strftime("%Y-%m-%d %H:%M"),
                startup_sentiment.get("sentiment", "SIDEWAYS"),
                generated_signal.signal,
            )
        _handle_generated_signal(symbol, generated_signal, startup_sentiment, premium_service, last_completed.close if last_completed else 0.0, trade_manager)

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

        _manage_active_trade(symbol, trade_manager, premium_service)

        sentiment = _get_market_sentiment(
            symbol=symbol,
            market_profile=market_profile,
            news_service=news_service,
        )

        generated = strategy.evaluate(sentiment)
        if generated is None:
            return

        try:
            store_signal(generated, database)
        except Exception as exc:
            logging.warning("Failed to store signal in DB: %s", exc)

        _handle_generated_signal(symbol, generated, sentiment, premium_service, candle.close, trade_manager)

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


def _handle_generated_signal(symbol: str, generated_signal, sentiment: dict[str, object], premium_service, spot_price: float, trade_manager: TradeManager) -> None:
    if generated_signal.signal == "BUY_CE":
        premium = _safe_premium_quote(premium_service, symbol, spot_price, "CALL")
        if premium is None:
            _print_premium_unavailable(symbol, "CE")
            return
        enriched_signal = enrich_signal_with_premium(generated_signal, premium)
        _register_trade_plan(symbol, enriched_signal, premium, trade_manager)
        _print_signal(enriched_signal, sentiment)
    elif generated_signal.signal == "BUY_PE":
        premium = _safe_premium_quote(premium_service, symbol, spot_price, "PUT")
        if premium is None:
            _print_premium_unavailable(symbol, "PE")
            return
        enriched_signal = enrich_signal_with_premium(generated_signal, premium)
        _register_trade_plan(symbol, enriched_signal, premium, trade_manager)
        _print_signal(enriched_signal, sentiment)
    elif generated_signal.signal in {"BUY", "SELL"}:
        _print_signal(generated_signal, sentiment)
    else:
        _log_no_trade(symbol, generated_signal.reason, sentiment)


def _safe_premium_quote(premium_service, symbol: str, spot_price: float, signal: str):
    try:
        return premium_service.get_premium_quote(symbol, spot_price, signal)
    except Exception as exc:
        logging.warning("Premium lookup failed for %s: %s", symbol, exc)
        return None

def _print_premium_unavailable(symbol: str, option_type: str) -> None:
    logging.warning("Unable to fetch option premium for %s %s", symbol, option_type)
    print(colorize(f"[WARNING] Unable to fetch option premium for {symbol} {option_type}", YELLOW, bold=True))

def _register_trade_plan(symbol: str, generated_signal, premium, trade_manager: TradeManager) -> None:
    if generated_signal.details is None or generated_signal.details.option_suggestion is None:
        return
    option = generated_signal.details.option_suggestion
    if option.entry_low is None or option.entry_high is None or option.stop_loss is None or option.target is None:
        return
    if trade_manager.has_active_trade(symbol):
        return
    trade_manager.open_trade_plan(
        symbol=symbol,
        signal=generated_signal.signal,
        trading_symbol=premium.trading_symbol,
        exchange=premium.exchange,
        option_type=premium.option_type or option.option_type,
        entry_low=option.entry_low,
        entry_high=option.entry_high,
        stop_loss=option.stop_loss,
        target=option.target,
        entry_price=None,
    )


def _manage_active_trade(symbol: str, trade_manager: TradeManager, premium_service) -> bool:
    active_trade = trade_manager.get_active_trade(symbol)
    if active_trade is None:
        return False

    premium = premium_service.get_contract_quote(active_trade.trading_symbol, active_trade.exchange)
    if premium is None:
        logging.warning("Unable to refresh premium for active trade %s", active_trade.trading_symbol)
        return True

    current_price = premium.last_price
    if active_trade.status == "PENDING_ENTRY":
        if active_trade.entry_low <= current_price <= active_trade.entry_high:
            activated_trade = trade_manager.update_active_trade(
                symbol,
                status="OPEN",
                entry_price=current_price,
            )
            _print_trade_management_update(activated_trade or active_trade, current_price, status="ENTRY TRIGGERED")
            return True

        if current_price < active_trade.stop_loss:
            trade_manager.close_active_trade(symbol, "entry_failed_below_stop", current_price)
            _print_trade_management_update(active_trade, current_price, status="ENTRY CANCELLED")
            return True

        _print_trade_management_update(active_trade, current_price, status="WAITING FOR ENTRY")
        return True

    entry_price = active_trade.entry_price or active_trade.entry_high
    if current_price <= active_trade.stop_loss:
        trade_manager.close_active_trade(symbol, "stop_loss_hit", current_price)
        _print_trade_management_update(active_trade, current_price, status="STOP LOSS HIT")
        return True
    if current_price >= active_trade.target:
        trade_manager.close_active_trade(symbol, "target_hit", current_price)
        _print_trade_management_update(active_trade, current_price, status="TARGET HIT")
        return True

    updated_trade = _trail_active_trade_if_needed(active_trade, current_price, trade_manager, entry_price)
    _print_trade_management_update(updated_trade or active_trade, current_price, status="HOLD")
    return True


def _trail_active_trade_if_needed(active_trade: ActiveTrade, current_price: float, trade_manager: TradeManager, entry_price: float) -> ActiveTrade | None:
    denominator = max(active_trade.target - entry_price, 0.01)
    progress_to_target = (current_price - entry_price) / denominator

    new_stop_loss = active_trade.stop_loss
    if current_price >= entry_price * 1.10:
        new_stop_loss = max(new_stop_loss, active_trade.entry_low)
    if progress_to_target >= 0.60:
        new_stop_loss = max(new_stop_loss, entry_price)

    if new_stop_loss <= active_trade.stop_loss:
        return None

    updated = trade_manager.update_active_trade(symbol=active_trade.symbol, stop_loss=new_stop_loss)
    if updated is not None:
        logging.info("[MANAGE] Trailed stop for %s to %.2f", active_trade.trading_symbol, new_stop_loss)
    return updated


def _print_trade_management_update(active_trade: ActiveTrade, current_price: float, status: str) -> None:
    entry_text = f"{active_trade.entry_low:.2f} - {active_trade.entry_high:.2f}"
    pnl_text = "NA"
    if active_trade.entry_price is not None and active_trade.entry_price > 0:
        pnl_pct = ((current_price - active_trade.entry_price) / active_trade.entry_price) * 100
        pnl_text = f"{pnl_pct:.2f}%"
        entry_text = f"{active_trade.entry_price:.2f} (filled within {active_trade.entry_low:.2f} - {active_trade.entry_high:.2f})"

    lines = [
        "----------------------------------------",
        f"ACTIVE TRADE - {active_trade.symbol}",
        "----------------------------------------",
        f"Status       : {status}",
        f"Contract     : {active_trade.trading_symbol}",
        f"Entry        : {entry_text}",
        f"Current LTP  : {current_price:.2f}",
        f"Stop Loss    : {active_trade.stop_loss:.2f}",
        f"Target       : {active_trade.target:.2f}",
        f"PnL %        : {pnl_text}",
        "----------------------------------------",
    ]
    color = YELLOW if status in {"HOLD", "WAITING FOR ENTRY", "ENTRY TRIGGERED"} else (GREEN if status == "TARGET HIT" else RED)
    print(colorize("\n".join(lines), color, bold=True))


def _print_signal(generated_signal, sentiment: dict[str, object]) -> None:
    color = GREEN if generated_signal.signal in {"BUY_CE", "BUY"} else RED
    if generated_signal.details is not None:
        print(colorize(format_output(generated_signal), color, bold=True))
        return

    summary = (
        f"[SIGNAL] {generated_signal.symbol} | Action: {_friendly_signal(generated_signal.signal)} "
        f"| Confidence: {_format_confidence(generated_signal.confidence)} "
        f"| Sentiment: {_friendly_sentiment(sentiment)}"
    )
    print(colorize(summary, color, bold=True))
    print(colorize(f"[WHY] {_humanize_reason(generated_signal.reason)}", color))


def _log_no_trade(symbol: str, reason: str, sentiment: dict[str, object]) -> None:
    details = _parse_reason_details(reason)
    logging.info(
        "[NO TRADE] %s | Market bias=%s | Entry trigger=%s | Sentiment=%s | Why=%s%s",
        symbol,
        details["market_bias"],
        details["entry_trigger"],
        _friendly_sentiment(sentiment),
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


def _friendly_sentiment(sentiment: dict[str, object]) -> str:
    sentiment_value = str(sentiment.get("sentiment", "SIDEWAYS")).strip().upper()
    mapping = {
        "BULLISH": "Bullish",
        "BEARISH": "Bearish",
        "SIDEWAYS": "Neutral",
    }
    return mapping.get(sentiment_value, sentiment_value.title())


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
        elif part.startswith("sentiment_mismatch="):
            value = part.split("=", 1)[1].replace("_", " ").title()
            readable.append(f"Sentiment is not aligned ({value})")
        elif part.startswith("sentiment_confirmed="):
            value = part.split("=", 1)[1].replace("_", " ").title()
            readable.append(f"Sentiment confirms the setup ({value})")
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
    elif any(part.startswith("sentiment_mismatch=") for part in parts):
        mismatch = next(
            part.split("=", 1)[1].replace("_", " ").title()
            for part in parts
            if part.startswith("sentiment_mismatch=")
        )
        entry_trigger = "Blocked"
        why = f"Sentiment did not confirm the setup ({mismatch})"
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


def _get_market_sentiment(symbol: str, market_profile, news_service) -> dict[str, object]:
    if not market_profile.uses_sentiment:
        return news_service.disabled_sentiment()

    headlines = fetch_or_get_cached_news(symbol, news_service)
    if not headlines:
        return {
            "sentiment": "SIDEWAYS",
            "confidence": 0.0,
            "reason": "no_headlines",
        }
    return get_sentiment_with_cache(symbol, headlines, news_service)


if __name__ == "__main__":
    main()



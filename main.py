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
from execution.trade_manager import TradeManager
from strategy.signal_engine import fetch_or_get_cached_news, get_sentiment_with_cache, store_market_data, store_signal
from strategy.strategy import LastClosedCandleStrategy


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

    logging.info("Runtime MODE=%s", mode)
    logging.info("[INFO] Processing SYMBOL: %s", symbol)

    market_data = MarketDataService(settings=settings)
    database = TradingDatabase()
    candle_manager = CandleManager(max_candles=100)
    loader = HistoricalDataLoader(market_data, database)
    news_service = NewsCacheService(database)
    premium_service = OptionPremiumService(market_data.clients.kite)
    trade_manager = TradeManager()
    order_manager = OrderManager(market_data.clients.kite, settings.execution, trade_manager)
    _ = order_manager

    historical_result = loader.fetch_historical_candles(symbol)
    candle_manager.initialize_candles(symbol, historical_result)
    if len(historical_result) < MINIMUM_STARTUP_CANDLES:
        logging.warning(
            "[INFO] Processing SYMBOL: %s | Waiting for sufficient data | loaded=%s candles",
            symbol,
            len(historical_result),
        )
    else:
        logging.info(
            "[INFO] Processing SYMBOL: %s | Historical backfill ready | candles=%s | last_completed=%s",
            symbol,
            len(historical_result),
            historical_result[-1].end.strftime("%Y-%m-%d %H:%M") if historical_result else "NA",
        )

    strategy = LastClosedCandleStrategy(candle_manager, symbol)

    startup_headlines = fetch_or_get_cached_news(symbol, news_service)
    startup_sentiment = get_sentiment_with_cache(symbol, startup_headlines, news_service) if startup_headlines else {
        "sentiment": "SIDEWAYS",
        "confidence": 0.0,
        "reason": "no_headlines",
    }

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
        _handle_generated_signal(symbol, generated_signal, startup_sentiment, premium_service, last_completed.close if last_completed else 0.0)

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

        headlines = fetch_or_get_cached_news(symbol, news_service)
        sentiment = get_sentiment_with_cache(symbol, headlines, news_service) if headlines else {
            "sentiment": "SIDEWAYS",
            "confidence": 0.0,
            "reason": "no_headlines",
        }

        generated = strategy.evaluate(sentiment)
        if generated is None:
            return

        try:
            store_signal(generated, database)
        except Exception as exc:
            logging.warning("Failed to store signal in DB: %s", exc)

        _handle_generated_signal(symbol, generated, sentiment, premium_service, candle.close)

    market_data.on_candle = handle_closed_candle

    logging.info(
        "Engine initialized | mode=%s | symbol=%s | candle=%s min",
        mode,
        symbol,
        settings.candle_interval_minutes,
    )
    logging.info("Historical backfill completed. Startup analysis finished using last completed candle.")
    print(colorize(f"[WAIT] Processing SYMBOL: {symbol} | waiting for live ticks and the next 5-minute candle close...", YELLOW, bold=True))

    market_data.start()


def _handle_generated_signal(symbol: str, generated_signal, sentiment: dict[str, object], premium_service, spot_price: float) -> None:
    if generated_signal.signal == "BUY_CE":
        premium = _safe_premium_quote(premium_service, symbol, spot_price, "CALL")
        _print_signal(generated_signal.signal, symbol, premium, sentiment, generated_signal.confidence, generated_signal.reason)
    elif generated_signal.signal == "BUY_PE":
        premium = _safe_premium_quote(premium_service, symbol, spot_price, "PUT")
        _print_signal(generated_signal.signal, symbol, premium, sentiment, generated_signal.confidence, generated_signal.reason)
    else:
        logging.info(
            "[INFO] Processing SYMBOL: %s | NO_TRADE | sentiment=%s | reason=%s",
            symbol,
            sentiment.get("sentiment", "SIDEWAYS"),
            generated_signal.reason,
        )


def _safe_premium_quote(premium_service, symbol: str, spot_price: float, signal: str):
    try:
        return premium_service.get_premium_quote(symbol, spot_price, signal)
    except Exception as exc:
        logging.warning("Premium lookup failed for %s: %s", symbol, exc)
        return None


def _print_signal(signal: str, symbol: str, premium, sentiment: dict[str, object], confidence: float, reason: str) -> None:
    color = GREEN if signal == "BUY_CE" else RED
    if premium is not None:
        message = (
            f"[{signal}] {symbol} | premium={premium.trading_symbol} @ {premium.last_price:.2f} "
            f"| sentiment={sentiment.get('sentiment')} | confidence={confidence:.2f}"
        )
    else:
        message = (
            f"[{signal}] {symbol} | premium=NA | sentiment={sentiment.get('sentiment')} "
            f"| confidence={confidence:.2f}"
        )
    print(colorize(message, color, bold=True))
    print(colorize(f"[REASON] {reason}", color))


if __name__ == "__main__":
    main()

# Zerodha Kite Algo Trading Starter

Simple Python project that connects to Zerodha Kite, streams live ticks with `KiteTicker`, builds 5-minute candles in memory, and prints OHLC whenever a candle closes.

## Structure

- `config/` settings and instrument configuration
- `data/` Kite client setup, live market data stream, and candle aggregation
- `strategy/` strategy hooks
- `execution/` order execution placeholder
- `main.py` application entrypoint

## Instruments

- `NIFTY` -> `NIFTY 50`
- `BANKNIFTY` -> `NIFTY BANK`
- `RELIANCE` -> `RELIANCE`
- `SBIN` -> `SBIN`

## Setup

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Copy `.env.example` to `.env` and fill in your Zerodha API credentials.
4. Run the app:

```bash
python main.py
```

## Execution Module

- `OrderManager.place_market_buy(...)` places a market buy using fixed capital sizing, then places a 20% protective stop-loss order.
- `OrderManager.check_order_status(order_id)` fetches the latest broker status for any order.
- `OrderManager.trail_stop_loss(...)` can be called as price moves to keep raising the stop loss without using a fixed target.

## Notes

- Instrument tokens are resolved at runtime from the Kite instruments master.
- Candles are generated from live tick prices and stored in memory.
- The WebSocket stream subscribes to multiple instruments, processes real-time ticks, builds 5-minute candles in memory, and triggers independent per-instrument signals on each candle close.
- Duplicate entries are blocked, and the strategy enforces at most one active trade per instrument until that trade is marked closed.

## n8n Workflow

- Import [n8n/indian_market_sentiment_workflow.json](/d:/trading_with_ashish/n8n/indian_market_sentiment_workflow.json) into n8n.
- Set `NEWS_API_KEY` and `OPENAI_API_KEY` in the n8n environment.
- The workflow runs every 5 minutes, fetches India-focused financial headlines, asks OpenAI whether sentiment is bullish or bearish for the Indian stock market, and returns `sentiment` plus `confidence_score`.
## Execution Mode

- Global mode is defined in `config/config.py` as `MODE = "PAPER"` by default.
- `PAPER` mode never calls Zerodha order APIs and simulates executions instead.
- `LIVE` mode places real orders through Kite and prints `⚠️ LIVE MODE ACTIVE - REAL MONEY TRADE` before order placement.


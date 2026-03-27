# Zerodha Kite Algo Trading Starter

Simple Python project that connects to Zerodha Kite, streams live ticks with `KiteTicker`, builds 3-minute candles in memory by default, and prints OHLC whenever a candle closes.

## Structure

- `config/` settings and instrument configuration
- `market_selector.py` market-aware instrument routing and profiles
- `data/` Kite client setup, live market data stream, and candle aggregation
- `strategy/` market-specific strategy hooks
- `execution/` order execution and option-selection helpers
- `main.py` application entrypoint

## Market Types

- `MARKET_TYPE=EQUITY` supports `NIFTY`, `BANKNIFTY`, `RELIANCE`, `SBIN`, `PETRONET`
- `MARKET_TYPE=MCX` supports `CRUDEOIL`, `NATURALGAS`, `GOLD`
- The same runtime pipeline is used for both markets, while instrument resolution and signal logic switch automatically from `.env`

## Setup

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Copy `.env.example` to `.env` and fill in your Zerodha API credentials.
4. Set `SYMBOL`, `MARKET_TYPE`, and optionally `CANDLE_INTERVAL_MINUTES=3` in `.env`.
5. Run the app:

```bash
python main.py
```

## Execution Module

- `OrderManager.place_market_buy(...)` places a market buy using fixed capital sizing, then places a protective stop-loss order.
- Quantity and stop-loss rounding adapt to the selected instrument's lot size and tick size.
- `EQUITY` mode can still resolve option premiums for supported index symbols.
- `MCX` mode uses directional commodity signals and does not request option premiums.

## Option Selection Engine

- `execution/option_selection_engine.py` selects the best option contract from an option chain.
- It uses premium filtering, OI analysis, volume, IV, ATM-based strike selection, and fixed 1:2 risk-reward targets.
- Entry point: `select_option_trade(option_chain, signal, market_type, ltp)`.
- Config flags supported from `.env`: `ENABLE_EQUITY`, `ENABLE_MCX`, `MIN_PREMIUM`, `MAX_PREMIUM`, `OPTION_STOP_LOSS_PCT`, `OPTION_RR_RATIO`.

## Notes

- Instrument tokens are resolved at runtime from the Kite instruments master.
- Candles are generated from live tick prices and stored in memory.
- The WebSocket stream subscribes to the configured instrument, processes real-time ticks, builds candles using `CANDLE_INTERVAL_MINUTES` from `.env` (default `3`), and triggers market-specific signals on each candle close.
- Duplicate entries are blocked, and the strategy enforces at most one active trade per instrument until that trade is marked closed.

from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from data.candle_store import Candle


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = BASE_DIR / "trading_system.db"
load_dotenv(BASE_DIR / ".env", override=False)


@dataclass(frozen=True)
class CachedSentiment:
    sentiment: str
    confidence: float


class TradingDatabase:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = str(db_path)
        self.connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._configure()
        self._create_tables()

    def _configure(self) -> None:
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute("PRAGMA busy_timeout=5000")

    def _create_tables(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS market_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume INTEGER NOT NULL,
                UNIQUE(symbol, timestamp)
            );

            CREATE TABLE IF NOT EXISTS news_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                headline TEXT NOT NULL,
                sentiment TEXT NOT NULL,
                confidence REAL NOT NULL,
                headline_hash TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                signal TEXT NOT NULL,
                reason TEXT NOT NULL,
                UNIQUE(symbol, timestamp)
            );

            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                trading_symbol TEXT NOT NULL,
                signal TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                quantity INTEGER NOT NULL,
                pnl REAL NOT NULL,
                pnl_pct REAL NOT NULL,
                exit_reason TEXT NOT NULL,
                regime TEXT,
                entry_reason TEXT,
                partial_exit_done INTEGER NOT NULL DEFAULT 0,
                realized_pnl REAL NOT NULL DEFAULT 0,
                rr_ratio REAL NOT NULL DEFAULT 0,
                target_price REAL,
                mfe_pct REAL,
                mae_pct REAL,
                opened_at TEXT,
                closed_at TEXT NOT NULL,
                duration_minutes REAL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        self.connection.commit()

    def store_market_data(self, candle: Candle) -> bool:
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO market_data (
                symbol, timestamp, open, high, low, close, volume
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candle.symbol,
                candle.end.isoformat(),
                candle.open,
                candle.high,
                candle.low,
                candle.close,
                candle.volume,
            ),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def get_last_closed_candle(self, symbol: str) -> Candle | None:
        row = self.connection.execute(
            """
            SELECT symbol, timestamp, open, high, low, close, volume
            FROM market_data
            WHERE symbol = ?
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (symbol,),
        ).fetchone()
        return self._row_to_candle(row) if row else None

    def get_recent_candles(self, symbol: str, limit: int = 25) -> list[Candle]:
        rows = self.connection.execute(
            """
            SELECT symbol, timestamp, open, high, low, close, volume
            FROM market_data
            WHERE symbol = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (symbol, limit),
        ).fetchall()
        return [self._row_to_candle(row) for row in reversed(rows)]

    def get_market_data_range(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        limit: int = 100,
    ) -> list[Candle]:
        rows = self.connection.execute(
            """
            SELECT symbol, timestamp, open, high, low, close, volume
            FROM market_data
            WHERE symbol = ? AND timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp ASC
            LIMIT ?
            """,
            (symbol, start.isoformat(), end.isoformat(), limit),
        ).fetchall()
        return [self._row_to_candle(row) for row in rows]

    def get_recent_news_headlines(self, max_age_minutes: int = 10, limit: int = 5) -> list[str]:
        cutoff = (datetime.now(UTC) - timedelta(minutes=max_age_minutes)).isoformat()
        rows = self.connection.execute(
            """
            SELECT headline
            FROM news_data
            WHERE timestamp >= ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (cutoff, limit),
        ).fetchall()
        return [str(row['headline']) for row in rows]

    def get_cached_sentiment(self, headlines: list[str]) -> CachedSentiment | None:
        if not headlines:
            return None

        hashes = [self.headline_hash(headline) for headline in headlines]
        placeholders = ','.join('?' for _ in hashes)
        rows = self.connection.execute(
            f"SELECT sentiment, confidence FROM news_data WHERE headline_hash IN ({placeholders})",
            hashes,
        ).fetchall()
        if len(rows) != len(hashes):
            return None

        counts = {"BULLISH": 0, "BEARISH": 0, "SIDEWAYS": 0}
        confidence_total = 0.0
        for row in rows:
            sentiment = str(row['sentiment']).upper()
            if sentiment not in counts:
                sentiment = "SIDEWAYS"
            counts[sentiment] += 1
            confidence_total += float(row['confidence'])

        sentiment = max(counts, key=counts.get)
        confidence = confidence_total / len(rows)
        return CachedSentiment(sentiment=sentiment, confidence=confidence)

    def store_news_data(
        self,
        headlines: list[str],
        sentiment: str,
        confidence: float,
        timestamp: datetime | None = None,
    ) -> None:
        ts = (timestamp or datetime.now(UTC)).isoformat()
        payload = [
            (ts, headline, sentiment.upper(), float(confidence), self.headline_hash(headline))
            for headline in headlines
        ]
        self.connection.executemany(
            """
            INSERT OR IGNORE INTO news_data (
                timestamp, headline, sentiment, confidence, headline_hash
            ) VALUES (?, ?, ?, ?, ?)
            """,
            payload,
        )
        self.connection.commit()

    def store_signal(self, symbol: str, timestamp: str, signal: str, reason: str) -> bool:
        cursor = self.connection.execute(
            """
            INSERT OR REPLACE INTO signals (symbol, timestamp, signal, reason)
            VALUES (?, ?, ?, ?)
            """,
            (symbol, timestamp, signal, reason),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    # NEW: persist closed-trade analytics for profitability review.
    def store_trade_summary(
        self,
        *,
        symbol: str,
        trading_symbol: str,
        signal: str,
        entry_price: float,
        exit_price: float,
        quantity: int,
        pnl: float,
        pnl_pct: float,
        exit_reason: str,
        regime: str,
        entry_reason: str,
        partial_exit_done: bool,
        realized_pnl: float,
        rr_ratio: float,
        target_price: float | None,
        mfe_pct: float | None,
        mae_pct: float | None,
        opened_at: str | None,
        closed_at: str,
        duration_minutes: float | None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO trades (
                symbol, trading_symbol, signal, entry_price, exit_price, quantity, pnl, pnl_pct,
                exit_reason, regime, entry_reason, partial_exit_done, realized_pnl, rr_ratio,
                target_price, mfe_pct, mae_pct, opened_at, closed_at, duration_minutes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol,
                trading_symbol,
                signal,
                float(entry_price),
                float(exit_price),
                int(quantity),
                float(pnl),
                float(pnl_pct),
                exit_reason,
                regime,
                entry_reason,
                1 if partial_exit_done else 0,
                float(realized_pnl),
                float(rr_ratio),
                target_price,
                mfe_pct,
                mae_pct,
                opened_at,
                closed_at,
                duration_minutes,
            ),
        )
        self.connection.commit()

    # IMPROVED: richer profitability statistics for live review.
    def get_trade_performance(self, symbol: str | None = None) -> dict[str, float]:
        where_clause = "WHERE symbol = ?" if symbol else ""
        params: tuple[object, ...] = (symbol,) if symbol else ()
        rows = self.connection.execute(
            f"""
            SELECT pnl, pnl_pct
            FROM trades
            {where_clause}
            ORDER BY closed_at ASC, id ASC
            """,
            params,
        ).fetchall()
        if not rows:
            return {
                "trades": 0.0,
                "wins": 0.0,
                "losses": 0.0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "avg_rr_proxy": 0.0,
                "avg_profit": 0.0,
                "avg_loss": 0.0,
                "expectancy": 0.0,
                "net_pnl": 0.0,
                "max_drawdown": 0.0,
            }

        pnls = [float(row["pnl"]) for row in rows]
        wins = [value for value in pnls if value > 0]
        losses = [value for value in pnls if value < 0]
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        equity = 0.0
        peak_equity = 0.0
        max_drawdown = 0.0
        for pnl in pnls:
            equity += pnl
            peak_equity = max(peak_equity, equity)
            max_drawdown = max(max_drawdown, peak_equity - equity)

        avg_win = gross_profit / len(wins) if wins else 0.0
        avg_loss = gross_loss / len(losses) if losses else 0.0
        avg_rr_proxy = (avg_win / avg_loss) if avg_loss > 0 else 0.0
        win_rate = (len(wins) / len(pnls)) * 100.0
        expectancy = (win_rate / 100.0 * avg_win) - ((1 - (win_rate / 100.0)) * avg_loss)
        return {
            "trades": float(len(pnls)),
            "wins": float(len(wins)),
            "losses": float(len(losses)),
            "win_rate": win_rate,
            "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else 0.0,
            "avg_rr_proxy": avg_rr_proxy,
            "avg_profit": avg_win,
            "avg_loss": avg_loss,
            "expectancy": expectancy,
            "net_pnl": sum(pnls),
            "max_drawdown": max_drawdown,
        }

    @staticmethod
    def headline_hash(headline: str) -> str:
        return hashlib.sha256(headline.strip().lower().encode("utf-8")).hexdigest()

    @staticmethod
    def _row_to_candle(row: sqlite3.Row) -> Candle:
        end = datetime.fromisoformat(str(row['timestamp']))
        timeframe_minutes = int(os.getenv("CANDLE_INTERVAL_MINUTES", "3"))
        start = end - timedelta(minutes=timeframe_minutes)
        return Candle(
            symbol=str(row['symbol']),
            start=start,
            end=end,
            open=float(row['open']),
            high=float(row['high']),
            low=float(row['low']),
            close=float(row['close']),
            volume=int(row['volume']),
        )

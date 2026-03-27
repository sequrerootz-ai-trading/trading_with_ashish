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

    def store_signal(self, symbol: str, timestamp: str, signal: str, reason: str) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO signals (symbol, timestamp, signal, reason)
            VALUES (?, ?, ?, ?)
            """,
            (symbol, timestamp, signal, reason),
        )
        self.connection.commit()

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

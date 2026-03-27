from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from urllib import error, parse, request

from dotenv import load_dotenv

from data.database import TradingDatabase
from strategy.market_sentiment import get_market_sentiment


logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=False)
NEWS_URL = "https://newsapi.org/v2/everything"


class NewsCacheService:
    def __init__(self, database: TradingDatabase, timeout_seconds: float = 2.0) -> None:
        self.database = database
        self.timeout_seconds = timeout_seconds
        self.news_api_key = os.getenv("NEWS_API_KEY", "").strip()

    def fetch_or_get_cached_news(self, symbol: str) -> list[str]:
        try:
            cached = self.database.get_recent_news_headlines(max_age_minutes=10, limit=5)
            if cached:
                logger.info("[INFO] Processing SYMBOL: %s | Using cached news headlines.", symbol)
                return cached
        except Exception as exc:
            logger.warning("Database news cache read failed for %s, falling back to live fetch: %s", symbol, exc)

        try:
            headlines = self._fetch_live_news(symbol)
            logger.info("[INFO] Processing SYMBOL: %s | Fetched %s fresh headlines.", symbol, len(headlines))
            return headlines
        except Exception as exc:
            logger.warning("Live news fetch failed for %s, returning empty headlines: %s", symbol, exc)
            return []

    def get_sentiment_with_cache(self, symbol: str, headlines: list[str]) -> dict[str, object]:
        try:
            cached = self.database.get_cached_sentiment(headlines)
            if cached is not None:
                logger.info("[INFO] Processing SYMBOL: %s | Using cached sentiment.", symbol)
                return {
                    "sentiment": cached.sentiment,
                    "confidence": cached.confidence,
                    "reason": "cached_headlines",
                }
        except Exception as exc:
            logger.warning("Database sentiment cache read failed for %s, using live model: %s", symbol, exc)

        sentiment = get_market_sentiment(headlines)
        try:
            self.database.store_news_data(
                headlines=headlines,
                sentiment=str(sentiment["sentiment"]),
                confidence=float(sentiment["confidence"]),
                timestamp=datetime.now(UTC),
            )
        except Exception as exc:
            logger.warning("Database news cache write failed for %s: %s", symbol, exc)
        return sentiment

    def _fetch_live_news(self, symbol: str) -> list[str]:
        if not self.news_api_key:
            raise ValueError("Missing NEWS_API_KEY")

        query = parse.urlencode(
            {
                "q": f"{symbol} stock news India",
                "language": "en",
                "sortBy": "publishedAt",
                "pageSize": 5,
            }
        )
        req = request.Request(
            url=f"{NEWS_URL}?{query}",
            headers={"X-Api-Key": self.news_api_key},
            method="GET",
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (TimeoutError, error.URLError, error.HTTPError, json.JSONDecodeError) as exc:
            raise RuntimeError(str(exc)) from exc

        articles = payload.get("articles", [])[:5]
        return [str(article.get("title", "")).strip() for article in articles if article.get("title")]

    @staticmethod
    def disabled_sentiment() -> dict[str, object]:
        return {
            "sentiment": "SIDEWAYS",
            "confidence": 0.0,
            "reason": "market_type_without_sentiment",
        }

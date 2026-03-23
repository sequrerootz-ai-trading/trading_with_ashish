from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib import error, parse, request

from dotenv import load_dotenv


logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=False)

NEWS_URL = "https://newsapi.org/v2/everything"
OPENAI_URL = "https://api.openai.com/v1/responses"
PROMPT = """Analyze these headlines for Indian stock market sentiment.

Return ONLY JSON:
{
  \"sentiment\": \"bullish\" | \"bearish\" | \"neutral\",
  \"score\": -1 to 1,
  \"confidence\": 0 to 100,
  \"reason\": \"short explanation\"
}"""


@dataclass(frozen=True)
class SentimentResult:
    sentiment: str
    score: float
    confidence: int
    reason: str = ""


class AISentimentAnalyzer:
    def __init__(
        self,
        news_api_key: str | None = None,
        openai_api_key: str | None = None,
        cache_minutes: int = 10,
        timeout_seconds: float = 2.0,
        model: str | None = None,
    ) -> None:
        self.news_api_key = (news_api_key or os.getenv("NEWS_API_KEY", "")).strip()
        self.openai_api_key = (openai_api_key or os.getenv("OPENAI_API_KEY", "")).strip()
        self.cache_ttl = timedelta(minutes=cache_minutes)
        self.timeout_seconds = timeout_seconds
        self.model = model or os.getenv("OPENAI_SENTIMENT_MODEL", "gpt-4.1-nano")

        self._lock = threading.Lock()
        self._cached_result = SentimentResult(
            sentiment="neutral",
            score=0.0,
            confidence=0,
            reason="fallback",
        )
        self._cached_at: datetime | None = None
        self._refreshing = False

    def get_sentiment(self) -> SentimentResult:
        with self._lock:
            cached_result = self._cached_result
            cached_at = self._cached_at
            should_refresh = self._is_stale(cached_at) and not self._refreshing
            if should_refresh:
                self._refreshing = True
                threading.Thread(target=self._refresh_cache, daemon=True).start()
            return cached_result

    def force_refresh(self) -> None:
        with self._lock:
            if self._refreshing:
                return
            self._refreshing = True
        threading.Thread(target=self._refresh_cache, daemon=True).start()

    def _refresh_cache(self) -> None:
        try:
            headlines = self._fetch_headlines()
            result = self._analyze_headlines(headlines)
        except Exception as exc:  # pragma: no cover - network failure path
            logger.warning("AI sentiment refresh failed: %s", exc)
            result = SentimentResult(
                sentiment="neutral",
                score=0.0,
                confidence=0,
                reason="fallback",
            )

        with self._lock:
            self._cached_result = result
            self._cached_at = datetime.now(UTC)
            self._refreshing = False

    def _fetch_headlines(self) -> list[str]:
        if not self.news_api_key:
            raise ValueError("Missing NEWS_API_KEY")

        query = parse.urlencode(
            {
                "q": '"Indian stock market" OR NIFTY OR BANKNIFTY OR SENSEX OR NSE OR BSE',
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
        payload = self._json_request(req)
        articles = payload.get("articles", [])[:5]
        headlines = [article.get("title", "").strip() for article in articles if article.get("title")]
        if not headlines:
            raise ValueError("No headlines available")
        return headlines

    def _analyze_headlines(self, headlines: list[str]) -> SentimentResult:
        if not self.openai_api_key:
            raise ValueError("Missing OPENAI_API_KEY")

        headline_block = "\n".join(f"- {headline}" for headline in headlines)
        body = {
            "model": self.model,
            "input": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": PROMPT,
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"Headlines:\n{headline_block}",
                        }
                    ],
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "indian_market_sentiment",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "sentiment": {
                                "type": "string",
                                "enum": ["bullish", "bearish", "neutral"],
                            },
                            "score": {
                                "type": "number",
                                "minimum": -1,
                                "maximum": 1,
                            },
                            "confidence": {
                                "type": "integer",
                                "minimum": 0,
                                "maximum": 100,
                            },
                            "reason": {"type": "string"},
                        },
                        "required": ["sentiment", "score", "confidence", "reason"],
                        "additionalProperties": False,
                    },
                }
            },
        }
        req = request.Request(
            url=OPENAI_URL,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.openai_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        payload = self._json_request(req)
        raw_text = self._extract_output_text(payload)
        parsed = json.loads(raw_text)

        score = float(parsed.get("score", 0.0))
        confidence = int(parsed.get("confidence", 0))
        reason = str(parsed.get("reason", ""))
        sentiment = self._normalize_sentiment(score)

        return SentimentResult(
            sentiment=sentiment,
            score=score,
            confidence=confidence,
            reason=reason,
        )

    def _json_request(self, req: request.Request) -> dict[str, Any]:
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except (TimeoutError, error.URLError, error.HTTPError, json.JSONDecodeError) as exc:
            raise RuntimeError(str(exc)) from exc

    @staticmethod
    def _extract_output_text(payload: dict[str, Any]) -> str:
        if isinstance(payload.get("output_text"), str):
            return payload["output_text"]

        for item in payload.get("output", []):
            for content in item.get("content", []):
                text = content.get("text")
                if isinstance(text, str):
                    return text

        raise ValueError("OpenAI response missing output text")

    @staticmethod
    def _normalize_sentiment(score: float) -> str:
        if score > 0.25:
            return "bullish"
        if score < -0.25:
            return "bearish"
        return "neutral"

    def _is_stale(self, cached_at: datetime | None) -> bool:
        if cached_at is None:
            return True
        return datetime.now(UTC) - cached_at >= self.cache_ttl

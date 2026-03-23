from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any
from urllib import error, request

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=False)

OPENAI_URL = "https://api.openai.com/v1/responses"
MODEL = "gpt-4.1-nano"
MAX_HEADLINES = 10
TIMEOUT_SECONDS = 2.0
MAX_OUTPUT_TOKENS = 50
SYSTEM_PROMPT = (
    "Classify Indian market news by headline majority. "
    "Positive majority=BULLISH negative majority=BEARISH mixed=SIDEWAYS. "
    "Return JSON only."
)


@dataclass(frozen=True)
class MarketSentiment:
    sentiment: str
    confidence: float
    reason: str


FALLBACK_SENTIMENT = MarketSentiment(
    sentiment="SIDEWAYS",
    confidence=0.0,
    reason="fallback",
)


def get_market_sentiment(headlines: list[str]) -> dict[str, Any]:
    cleaned_headlines = _prepare_headlines(headlines)
    if not cleaned_headlines:
        return asdict(FALLBACK_SENTIMENT)

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return asdict(FALLBACK_SENTIMENT)

    try:
        payload = _build_payload(cleaned_headlines)
        response = _post_json(payload, api_key)
        content = _extract_output_text(response)
        parsed = json.loads(content)
        return _normalize_response(parsed)
    except Exception:
        return asdict(FALLBACK_SENTIMENT)


def _prepare_headlines(headlines: list[str]) -> list[str]:
    cleaned: list[str] = []
    for headline in headlines[:MAX_HEADLINES]:
        normalized = " ".join(str(headline).split()).strip()
        if normalized:
            cleaned.append(normalized[:160])
    return cleaned


def _build_payload(headlines: list[str]) -> dict[str, Any]:
    batched_headlines = "\n".join(f"- {headline}" for headline in headlines)
    return {
        "model": MODEL,
        "temperature": 0,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": SYSTEM_PROMPT}],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Headlines:\n"
                            f"{batched_headlines}\n"
                            'JSON:{"sentiment":"BULLISH|BEARISH|SIDEWAYS","confidence":0-1,"reason":"one line"}'
                        ),
                    }
                ],
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "market_sentiment",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "sentiment": {
                            "type": "string",
                            "enum": ["BULLISH", "BEARISH", "SIDEWAYS"],
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                        },
                        "reason": {"type": "string"},
                    },
                    "required": ["sentiment", "confidence", "reason"],
                    "additionalProperties": False,
                },
            }
        },
    }


def _post_json(payload: dict[str, Any], api_key: str) -> dict[str, Any]:
    req = request.Request(
        url=OPENAI_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except (TimeoutError, error.URLError, error.HTTPError, json.JSONDecodeError) as exc:
        raise RuntimeError(str(exc)) from exc


def _extract_output_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]

    for item in payload.get("output", []):
        for content in item.get("content", []):
            text = content.get("text")
            if isinstance(text, str):
                return text

    raise ValueError("OpenAI response missing output text")


def _normalize_response(parsed: dict[str, Any]) -> dict[str, Any]:
    sentiment = str(parsed.get("sentiment", "SIDEWAYS")).upper()
    confidence = float(parsed.get("confidence", 0.0))
    reason = str(parsed.get("reason", "fallback")).strip() or "fallback"

    if sentiment not in {"BULLISH", "BEARISH", "SIDEWAYS"}:
        sentiment = "SIDEWAYS"
    confidence = min(max(confidence, 0.0), 1.0)

    return {
        "sentiment": sentiment,
        "confidence": confidence,
        "reason": reason[:120],
    }

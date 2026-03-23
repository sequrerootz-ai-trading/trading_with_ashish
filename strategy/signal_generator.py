from __future__ import annotations

from dataclasses import dataclass


TECHNICAL_TO_SENTIMENT = {
    "CALL": "bullish",
    "PUT": "bearish",
    "BULLISH": "bullish",
    "BEARISH": "bearish",
}


@dataclass(frozen=True)
class FinalSignal:
    final_signal: str | None
    confidence_score: float


def normalize_technical_signal(signal: str) -> str:
    normalized_signal = signal.strip().upper()
    if normalized_signal not in TECHNICAL_TO_SENTIMENT:
        raise ValueError(f"Unsupported technical signal: {signal}")
    return normalized_signal


def normalize_sentiment(sentiment: str) -> str:
    normalized_sentiment = sentiment.strip().lower()
    if normalized_sentiment not in {"bullish", "bearish"}:
        raise ValueError(f"Unsupported sentiment: {sentiment}")
    return normalized_sentiment


def generate_final_signal(technical_signal: str, sentiment: str) -> FinalSignal:
    normalized_technical_signal = normalize_technical_signal(technical_signal)
    normalized_sentiment = normalize_sentiment(sentiment)
    technical_bias = TECHNICAL_TO_SENTIMENT[normalized_technical_signal]

    if technical_bias != normalized_sentiment:
        return FinalSignal(final_signal=None, confidence_score=0.0)

    return FinalSignal(final_signal=normalized_technical_signal, confidence_score=1.0)

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

    # -------------------------------
    # Improved Confidence Logic (SAFE)
    # -------------------------------

    confidence = 0.0

    # 1. Agreement weight
    if technical_bias == normalized_sentiment:
        confidence += 0.6
    else:
        confidence -= 0.4  # penalize mismatch

    # 2. Base reliability (keeps system stable)
    confidence += 0.2

    # Normalize confidence between 0 and 1
    confidence = max(0.0, min(1.0, confidence))

    # -------------------------------
    # Decision Logic (Backward Compatible)
    # -------------------------------

    # Maintain original behavior: no trade if mismatch
    if technical_bias != normalized_sentiment:
        return FinalSignal(final_signal=None, confidence_score=confidence)

    # Add minimum confidence threshold (prevents weak trades)
    if confidence < 0.5:
        return FinalSignal(final_signal=None, confidence_score=confidence)

    return FinalSignal(
        final_signal=normalized_technical_signal,
        confidence_score=confidence,
    )
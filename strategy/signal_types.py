from __future__ import annotations

from dataclasses import dataclass, field

from data.candle_store import Candle


@dataclass(frozen=True)
class SignalContext:
    symbol: str
    candles: list[Candle]
    last_candle: Candle | None
    timeframe_minutes: int = 5


@dataclass(frozen=True)
class IndicatorDetails:
    ema_9: float | None = None
    ema_21: float | None = None
    rsi: float | None = None
    trend: str = "neutral"
    trend_strength_pct: float = 0.0
    breakout_price: float | None = None
    breakdown_price: float | None = None
    volume_ratio: float | None = None
    market_condition: str = "neutral"
    rsi_state: str = "normal"


@dataclass(frozen=True)
class ValidationItem:
    label: str
    status: str
    passed: bool


@dataclass(frozen=True)
class OptionSuggestion:
    strike: int | None
    option_type: str
    label: str
    premium_ltp: float | None = None
    trading_symbol: str | None = None
    entry_low: float | None = None
    entry_high: float | None = None
    stop_loss: float | None = None
    target: float | None = None


@dataclass(frozen=True)
class SignalDetails:
    action_label: str
    confidence_pct: int
    confidence_label: str
    risk_label: str
    indicator_details: IndicatorDetails
    validations: list[ValidationItem] = field(default_factory=list)
    option_suggestion: OptionSuggestion | None = None
    summary: str = ""


@dataclass(frozen=True)
class GeneratedSignal:
    symbol: str
    timestamp: str
    signal: str
    reason: str
    confidence: float
    details: SignalDetails | None = None

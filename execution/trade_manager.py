from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TradeRecord:
    mode: str
    symbol: str
    side: str
    quantity: int
    price: float
    status: str
    created_at: str


@dataclass(frozen=True)
class ActiveTrade:
    symbol: str
    signal: str
    trading_symbol: str
    exchange: str
    option_type: str
    entry_low: float
    entry_high: float
    stop_loss: float
    target: float
    entry_price: float | None
    status: str
    created_at: str


class TradeManager:
    def __init__(self) -> None:
        self._trade_log: list[TradeRecord] = []
        self._active_trades: dict[str, ActiveTrade] = {}

    def record_trade(
        self,
        mode: str,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
        status: str,
    ) -> TradeRecord:
        record = TradeRecord(
            mode=mode,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            status=status,
            created_at=datetime.now(UTC).isoformat(),
        )
        self._trade_log.append(record)
        logger.info("[%s] %s %s @ %.2f qty=%s status=%s", mode, side, symbol, price, quantity, status)
        return record

    def open_trade_plan(
        self,
        symbol: str,
        signal: str,
        trading_symbol: str,
        exchange: str,
        option_type: str,
        entry_low: float,
        entry_high: float,
        stop_loss: float,
        target: float,
        entry_price: float | None,
    ) -> ActiveTrade:
        trade = ActiveTrade(
            symbol=symbol,
            signal=signal,
            trading_symbol=trading_symbol,
            exchange=exchange,
            option_type=option_type,
            entry_low=entry_low,
            entry_high=entry_high,
            stop_loss=stop_loss,
            target=target,
            entry_price=entry_price,
            status="PENDING_ENTRY",
            created_at=datetime.now(UTC).isoformat(),
        )
        self._active_trades[symbol] = trade
        logger.info(
            "[PLAN] Opened trade plan for %s | %s | %s | entry=%.2f-%.2f | sl=%.2f | target=%.2f",
            symbol,
            signal,
            trading_symbol,
            entry_low,
            entry_high,
            stop_loss,
            target,
        )
        return trade

    def get_active_trade(self, symbol: str) -> ActiveTrade | None:
        return self._active_trades.get(symbol)

    def has_active_trade(self, symbol: str) -> bool:
        return symbol in self._active_trades

    def update_active_trade(self, symbol: str, **changes: float | str) -> ActiveTrade | None:
        trade = self._active_trades.get(symbol)
        if trade is None:
            return None
        updated = ActiveTrade(
            symbol=trade.symbol,
            signal=str(changes.get("signal", trade.signal)),
            trading_symbol=str(changes.get("trading_symbol", trade.trading_symbol)),
            exchange=str(changes.get("exchange", trade.exchange)),
            option_type=str(changes.get("option_type", trade.option_type)),
            entry_low=float(changes.get("entry_low", trade.entry_low)),
            entry_high=float(changes.get("entry_high", trade.entry_high)),
            stop_loss=float(changes.get("stop_loss", trade.stop_loss)),
            target=float(changes.get("target", trade.target)),
            entry_price=(float(changes["entry_price"]) if "entry_price" in changes and changes["entry_price"] is not None else trade.entry_price),
            status=str(changes.get("status", trade.status)),
            created_at=trade.created_at,
        )
        self._active_trades[symbol] = updated
        return updated

    def close_active_trade(self, symbol: str, reason: str, exit_price: float) -> ActiveTrade | None:
        trade = self._active_trades.pop(symbol, None)
        if trade is None:
            return None
        logger.info(
            "[PLAN] Closed trade plan for %s | %s | exit=%.2f | reason=%s",
            symbol,
            trade.trading_symbol,
            exit_price,
            reason,
        )
        return trade

    def get_trade_log(self) -> list[TradeRecord]:
        return list(self._trade_log)

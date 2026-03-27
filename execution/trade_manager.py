from __future__ import annotations

import logging
import uuid
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
    initial_stop_loss: float
    quantity: int
    remaining_quantity: int
    entry_price: float | None
    highest_price: float | None
    order_placed: bool
    entry_order_id: str | None
    stop_loss_order_id: str | None
    partial_exit_done: bool
    regime: str
    entry_reason: str
    rr_ratio: float
    confirmation_high: float | None
    confirmation_low: float | None
    opened_at: str | None
    status: str
    created_at: str


class TradeManager:
    def __init__(self) -> None:
        self._trade_log: list[TradeRecord] = []
        self._active_trades: dict[str, ActiveTrade] = {}

        # NEW (safe): optional mapping for future scalability
        self._trade_ids: dict[str, str] = {}

    # -------------------------------------------
    # INTERNAL HELPERS (SAFE ADDITIONS)
    # -------------------------------------------

    def _generate_trade_id(self) -> str:
        return str(uuid.uuid4())

    def _calculate_pnl(self, side: str, entry: float, exit: float, qty: int) -> float:
        """Supports BUY and SELL without breaking existing logic."""
        try:
            if side.upper() == "BUY":
                return (exit - entry) * qty
            elif side.upper() == "SELL":
                return (entry - exit) * qty
        except Exception as e:
            logger.error("[PNL] Error calculating pnl: %s", e)
        return 0.0

    def _safe_float(self, value, default: float) -> float:
        try:
            return float(value)
        except Exception:
            return default

    def _safe_int(self, value, default: int) -> int:
        try:
            return int(value)
        except Exception:
            return default

    # -------------------------------------------
    # EXISTING METHODS (UNCHANGED BEHAVIOR)
    # -------------------------------------------

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

        logger.info(
            "[%s] %s %s @ %.2f qty=%s status=%s",
            mode, side, symbol, price, quantity, status
        )

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
        entry_price: float | None,
        regime: str = "UNKNOWN",
        entry_reason: str = "",
        rr_ratio: float = 1.0,
        confirmation_high: float | None = None,
        confirmation_low: float | None = None,
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
            initial_stop_loss=stop_loss,
            quantity=0,
            remaining_quantity=0,
            entry_price=entry_price,
            highest_price=entry_price,
            order_placed=False,
            entry_order_id=None,
            stop_loss_order_id=None,
            partial_exit_done=False,
            regime=regime,
            entry_reason=entry_reason,
            rr_ratio=rr_ratio,
            confirmation_high=confirmation_high,
            confirmation_low=confirmation_low,
            opened_at=None,
            status="PENDING_ENTRY",
            created_at=datetime.now(UTC).isoformat(),
        )

        self._active_trades[symbol] = trade
        self._trade_ids[symbol] = self._generate_trade_id()

        logger.info(
            "[PLAN] Opened trade plan for %s | %s | %s | entry=%.2f-%.2f | sl=%.2f | regime=%s",
            symbol,
            signal,
            trading_symbol,
            entry_low,
            entry_high,
            stop_loss,
            regime,
        )

        return trade

    def get_active_trade(self, symbol: str) -> ActiveTrade | None:
        return self._active_trades.get(symbol)

    def has_active_trade(self, symbol: str) -> bool:
        return symbol in self._active_trades

    def update_active_trade(self, symbol: str, **changes) -> ActiveTrade | None:
        trade = self._active_trades.get(symbol)
        if trade is None:
            return None

        updated = ActiveTrade(
            symbol=trade.symbol,
            signal=str(changes.get("signal", trade.signal)),
            trading_symbol=str(changes.get("trading_symbol", trade.trading_symbol)),
            exchange=str(changes.get("exchange", trade.exchange)),
            option_type=str(changes.get("option_type", trade.option_type)),
            entry_low=self._safe_float(changes.get("entry_low", trade.entry_low), trade.entry_low),
            entry_high=self._safe_float(changes.get("entry_high", trade.entry_high), trade.entry_high),
            stop_loss=self._safe_float(changes.get("stop_loss", trade.stop_loss), trade.stop_loss),
            initial_stop_loss=self._safe_float(changes.get("initial_stop_loss", trade.initial_stop_loss), trade.initial_stop_loss),
            quantity=self._safe_int(changes.get("quantity", trade.quantity), trade.quantity),
            remaining_quantity=self._safe_int(changes.get("remaining_quantity", trade.remaining_quantity), trade.remaining_quantity),
            entry_price=(
                self._safe_float(changes["entry_price"], trade.entry_price)
                if "entry_price" in changes and changes["entry_price"] is not None
                else trade.entry_price
            ),
            highest_price=(
                self._safe_float(changes["highest_price"], trade.highest_price)
                if "highest_price" in changes and changes["highest_price"] is not None
                else trade.highest_price
            ),
            order_placed=bool(changes.get("order_placed", trade.order_placed)),
            entry_order_id=(
                str(changes["entry_order_id"])
                if "entry_order_id" in changes and changes["entry_order_id"] is not None
                else trade.entry_order_id
            ),
            stop_loss_order_id=(
                str(changes["stop_loss_order_id"])
                if "stop_loss_order_id" in changes and changes["stop_loss_order_id"] is not None
                else trade.stop_loss_order_id
            ),
            partial_exit_done=bool(changes.get("partial_exit_done", trade.partial_exit_done)),
            regime=str(changes.get("regime", trade.regime)),
            entry_reason=str(changes.get("entry_reason", trade.entry_reason)),
            rr_ratio=self._safe_float(changes.get("rr_ratio", trade.rr_ratio), trade.rr_ratio),
            confirmation_high=(
                self._safe_float(changes["confirmation_high"], trade.confirmation_high)
                if "confirmation_high" in changes and changes["confirmation_high"] is not None
                else trade.confirmation_high
            ),
            confirmation_low=(
                self._safe_float(changes["confirmation_low"], trade.confirmation_low)
                if "confirmation_low" in changes and changes["confirmation_low"] is not None
                else trade.confirmation_low
            ),
            opened_at=(
                str(changes["opened_at"])
                if "opened_at" in changes and changes["opened_at"] is not None
                else trade.opened_at
            ),
            status=str(changes.get("status", trade.status)),
            created_at=trade.created_at,
        )

        self._active_trades[symbol] = updated
        return updated

    def close_active_trade(self, symbol: str, reason: str, exit_price: float) -> ActiveTrade | None:
        trade = self._active_trades.pop(symbol, None)
        self._trade_ids.pop(symbol, None)

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

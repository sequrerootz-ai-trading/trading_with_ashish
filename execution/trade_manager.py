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
    reason: str
    order_id: str | None
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
    target_price: float | None
    confirmation_high: float | None
    confirmation_low: float | None
    opened_at: str | None
    realized_pnl: float
    mfe_price: float | None
    mae_price: float | None
    exit_reason: str | None
    status: str
    created_at: str


class TradeManager:
    # NEW: tracks richer lifecycle state without altering signal logic.
    def __init__(self) -> None:
        self._trade_log: list[TradeRecord] = []
        self._active_trades: dict[str, ActiveTrade] = {}

        # NEW: optional mapping for future scalability.
        self._trade_ids: dict[str, str] = {}

    def _generate_trade_id(self) -> str:
        return str(uuid.uuid4())

    def _safe_float(self, value, default: float | None) -> float | None:
        try:
            return float(value)
        except Exception:
            return default

    def _safe_int(self, value, default: int) -> int:
        try:
            return int(value)
        except Exception:
            return default

    def record_trade(
        self,
        mode: str,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
        status: str,
        reason: str = "",
        order_id: str | None = None,
    ) -> TradeRecord:
        record = TradeRecord(
            mode=mode,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            status=status,
            reason=reason,
            order_id=order_id,
            created_at=datetime.now(UTC).isoformat(),
        )
        self._trade_log.append(record)

        logger.info(
            "[%s] %s %s @ %.2f qty=%s status=%s reason=%s order_id=%s",
            mode,
            side,
            symbol,
            price,
            quantity,
            status,
            reason or "-",
            order_id or "-",
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
        target_price: float | None = None,
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
            target_price=target_price,
            confirmation_high=confirmation_high,
            confirmation_low=confirmation_low,
            opened_at=None,
            realized_pnl=0.0,
            mfe_price=entry_price,
            mae_price=entry_price,
            exit_reason=None,
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
            entry_low=float(self._safe_float(changes.get("entry_low", trade.entry_low), trade.entry_low) or trade.entry_low),
            entry_high=float(self._safe_float(changes.get("entry_high", trade.entry_high), trade.entry_high) or trade.entry_high),
            stop_loss=float(self._safe_float(changes.get("stop_loss", trade.stop_loss), trade.stop_loss) or trade.stop_loss),
            initial_stop_loss=float(
                self._safe_float(changes.get("initial_stop_loss", trade.initial_stop_loss), trade.initial_stop_loss)
                or trade.initial_stop_loss
            ),
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
            rr_ratio=float(self._safe_float(changes.get("rr_ratio", trade.rr_ratio), trade.rr_ratio) or trade.rr_ratio),
            target_price=(
                self._safe_float(changes["target_price"], trade.target_price)
                if "target_price" in changes and changes["target_price"] is not None
                else trade.target_price
            ),
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
            realized_pnl=float(self._safe_float(changes.get("realized_pnl", trade.realized_pnl), trade.realized_pnl) or 0.0),
            mfe_price=(
                self._safe_float(changes["mfe_price"], trade.mfe_price)
                if "mfe_price" in changes and changes["mfe_price"] is not None
                else trade.mfe_price
            ),
            mae_price=(
                self._safe_float(changes["mae_price"], trade.mae_price)
                if "mae_price" in changes and changes["mae_price"] is not None
                else trade.mae_price
            ),
            exit_reason=(
                str(changes["exit_reason"])
                if "exit_reason" in changes and changes["exit_reason"] is not None
                else trade.exit_reason
            ),
            status=str(changes.get("status", trade.status)),
            created_at=trade.created_at,
        )

        self._active_trades[symbol] = updated
        return updated

    # NEW: helper for closed-trade snapshots without mutating active state.
    def update_trade_snapshot(self, trade: ActiveTrade, **changes) -> ActiveTrade:
        previous_trade = self._active_trades.get(trade.symbol)
        self._active_trades[trade.symbol] = trade
        try:
            updated = self.update_active_trade(trade.symbol, **changes)
        finally:
            if previous_trade is None:
                self._active_trades.pop(trade.symbol, None)
            else:
                self._active_trades[trade.symbol] = previous_trade
        return updated or trade

    def close_active_trade(self, symbol: str, reason: str, exit_price: float) -> ActiveTrade | None:
        trade = self._active_trades.pop(symbol, None)
        self._trade_ids.pop(symbol, None)

        if trade is None:
            return None

        closed_trade = self.update_trade_snapshot(
            trade,
            status="CLOSED",
            exit_reason=reason,
        )

        logger.info(
            "[PLAN] Closed trade plan for %s | %s | exit=%.2f | reason=%s",
            symbol,
            closed_trade.trading_symbol,
            exit_price,
            reason,
        )

        return closed_trade

    def get_trade_log(self) -> list[TradeRecord]:
        return list(self._trade_log)

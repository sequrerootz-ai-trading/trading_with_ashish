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


class TradeManager:
    def __init__(self) -> None:
        self._trade_log: list[TradeRecord] = []

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

    def get_trade_log(self) -> list[TradeRecord]:
        return list(self._trade_log)

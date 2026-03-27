from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from kiteconnect import KiteConnect

from config import get_mode
from config.settings import ExecutionSettings, InstrumentConfig
from execution.trade_manager import TradeManager


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ManagedOrder:
    trading_symbol: str
    exchange: str
    quantity: int
    entry_order_id: str
    stop_loss_order_id: str
    entry_price: float
    stop_loss_price: float
    status: str


class OrderManager:
    def __init__(
        self,
        kite: KiteConnect | None,
        settings: ExecutionSettings,
        instrument: InstrumentConfig | None = None,
        trade_manager: TradeManager | None = None,
    ) -> None:
        self.kite = kite
        self.settings = settings
        self.instrument = instrument
        self.mode = get_mode()
        self.trade_manager = trade_manager or TradeManager()
        self._paper_orders: dict[str, dict[str, Any]] = {}
        self._paper_order_count = 0

    def calculate_quantity(self, last_price: float) -> int:
        if last_price <= 0:
            raise ValueError("Last price must be greater than 0 for quantity calculation.")

        lot_size = self.instrument.lot_size if self.instrument is not None else 1
        units = int(self.settings.capital_per_trade // last_price)
        if lot_size <= 1:
            return max(units, 1)

        lots = max(units // lot_size, 1)
        return lots * lot_size

    def place_market_buy(
        self,
        trading_symbol: str,
        exchange: str,
        last_price: float,
        stop_loss_price: float | None = None,
        product: str | None = None,
    ) -> ManagedOrder:
        quantity = self.calculate_quantity(last_price)
        product_to_use = (product or self.settings.default_product).upper()
        stop_price_to_use = self._round_to_tick(
            stop_loss_price if stop_loss_price is not None else self._calculate_stop_loss_price(last_price)
        )

        if self.mode == "PAPER":
            return self._simulate_market_buy(
                trading_symbol=trading_symbol,
                exchange=exchange,
                last_price=last_price,
                quantity=quantity,
                stop_loss_price=stop_price_to_use,
                product=product_to_use,
            )

        self._ensure_live_ready()
        entry_order_id = self._with_retry(
            lambda: self.kite.place_order(
                variety=self.kite.VARIETY_REGULAR,
                exchange=exchange,
                tradingsymbol=trading_symbol,
                transaction_type=self.kite.TRANSACTION_TYPE_BUY,
                quantity=quantity,
                order_type=self.kite.ORDER_TYPE_MARKET,
                product=product_to_use,
                validity=self.kite.VALIDITY_DAY,
            ),
            action_name=f"place market BUY for {trading_symbol}",
        )

        entry_status = self.wait_for_order_completion(entry_order_id)
        entry_price = self._extract_average_price(entry_status, fallback_price=last_price)
        live_stop_loss_price = self._round_to_tick(stop_price_to_use)

        try:
            stop_loss_order_id = self._with_retry(
                lambda: self.kite.place_order(
                    variety=self.kite.VARIETY_REGULAR,
                    exchange=exchange,
                    tradingsymbol=trading_symbol,
                    transaction_type=self.kite.TRANSACTION_TYPE_SELL,
                    quantity=quantity,
                    order_type=self.kite.ORDER_TYPE_SLM,
                    trigger_price=live_stop_loss_price,
                    product=product_to_use,
                    validity=self.kite.VALIDITY_DAY,
                ),
                action_name=f"place stop-loss order for {trading_symbol}",
            )
        except Exception:
            logger.exception("Stop-loss placement failed after BUY fill for %s. Attempting emergency exit.", trading_symbol)
            self.exit_position(
                trading_symbol=trading_symbol,
                exchange=exchange,
                quantity=quantity,
                last_price=entry_price,
                product=product_to_use,
            )
            raise

        self.trade_manager.record_trade(
            mode="LIVE",
            symbol=trading_symbol,
            side="BUY",
            quantity=quantity,
            price=entry_price,
            status="COMPLETE",
        )
        logger.info("[LIVE] Order placed %s @ %.2f", trading_symbol, entry_price)

        return ManagedOrder(
            trading_symbol=trading_symbol,
            exchange=exchange,
            quantity=quantity,
            entry_order_id=entry_order_id,
            stop_loss_order_id=stop_loss_order_id,
            entry_price=entry_price,
            stop_loss_price=live_stop_loss_price,
            status=entry_status["status"],
        )

    def check_order_status(self, order_id: str) -> dict[str, Any]:
        if self.mode == "PAPER":
            order = self._paper_orders.get(order_id)
            if order is None:
                raise ValueError(f"Order not found: {order_id}")
            return dict(order)

        self._ensure_live_ready()
        orders = self._with_retry(
            self.kite.orders,
            action_name=f"fetch order book for {order_id}",
        )
        order = next((item for item in orders if item["order_id"] == order_id), None)
        if order is None:
            raise ValueError(f"Order not found: {order_id}")
        return order

    def wait_for_order_completion(self, order_id: str) -> dict[str, Any]:
        if self.mode == "PAPER":
            return self.check_order_status(order_id)

        terminal_statuses = {"COMPLETE", "REJECTED", "CANCELLED"}

        for attempt in range(1, self.settings.poll_attempts + 1):
            order = self.check_order_status(order_id)
            status = order.get("status", "UNKNOWN")
            logger.info(
                "Order %s status check %s/%s -> %s",
                order_id,
                attempt,
                self.settings.poll_attempts,
                status,
            )
            if status in terminal_statuses:
                if status != "COMPLETE":
                    raise RuntimeError(f"Order {order_id} ended with status {status}")
                return order
            time.sleep(self.settings.poll_interval_seconds)

        raise TimeoutError(f"Order {order_id} did not complete in time.")

    def exit_position(
        self,
        trading_symbol: str,
        exchange: str,
        quantity: int,
        last_price: float,
        product: str | None = None,
    ) -> dict[str, Any]:
        product_to_use = (product or self.settings.default_product).upper()

        if self.mode == "PAPER":
            order_id = self._next_paper_order_id("EXIT")
            self._paper_orders[order_id] = {
                "order_id": order_id,
                "status": "COMPLETE",
                "average_price": last_price,
                "price": last_price,
                "tradingsymbol": trading_symbol,
                "exchange": exchange,
                "transaction_type": "SELL",
                "product": product_to_use,
                "quantity": quantity,
            }
            self.trade_manager.record_trade(
                mode="PAPER",
                symbol=trading_symbol,
                side="SELL",
                quantity=quantity,
                price=last_price,
                status="SIMULATED_EXIT",
            )
            return dict(self._paper_orders[order_id])

        self._ensure_live_ready()
        order_id = self._with_retry(
            lambda: self.kite.place_order(
                variety=self.kite.VARIETY_REGULAR,
                exchange=exchange,
                tradingsymbol=trading_symbol,
                transaction_type=self.kite.TRANSACTION_TYPE_SELL,
                quantity=quantity,
                order_type=self.kite.ORDER_TYPE_MARKET,
                product=product_to_use,
                validity=self.kite.VALIDITY_DAY,
            ),
            action_name=f"place market SELL for {trading_symbol}",
        )
        order = self.wait_for_order_completion(order_id)
        exit_price = self._extract_average_price(order, fallback_price=last_price)
        self.trade_manager.record_trade(
            mode="LIVE",
            symbol=trading_symbol,
            side="SELL",
            quantity=quantity,
            price=exit_price,
            status="COMPLETE",
        )
        return order

    def trail_stop_loss(
        self,
        trading_symbol: str,
        exchange: str,
        quantity: int,
        last_price: float,
        stop_loss_order_id: str,
        current_stop_loss_price: float,
        product: str | None = None,
    ) -> float:
        new_stop_loss_price = self._calculate_stop_loss_price(
            last_price,
            percent=self.settings.trailing_stop_loss_percent,
        )
        return self.trail_stop_loss_to_price(
            trading_symbol=trading_symbol,
            exchange=exchange,
            quantity=quantity,
            stop_loss_order_id=stop_loss_order_id,
            current_stop_loss_price=current_stop_loss_price,
            new_stop_loss_price=new_stop_loss_price,
            product=product,
        )

    def trail_stop_loss_to_price(
        self,
        trading_symbol: str,
        exchange: str,
        quantity: int,
        stop_loss_order_id: str,
        current_stop_loss_price: float,
        new_stop_loss_price: float,
        product: str | None = None,
    ) -> float:
        rounded_stop_loss = self._round_to_tick(new_stop_loss_price)
        if rounded_stop_loss <= current_stop_loss_price:
            return current_stop_loss_price

        if self.mode == "PAPER":
            paper_order = self._paper_orders.get(stop_loss_order_id)
            if paper_order is not None:
                paper_order["trigger_price"] = rounded_stop_loss
                paper_order["price"] = rounded_stop_loss
            logger.info("[PAPER] Trailed stop loss %s to %.2f", trading_symbol, rounded_stop_loss)
            return rounded_stop_loss

        self._ensure_live_ready()
        product_to_use = (product or self.settings.default_product).upper()
        self._with_retry(
            lambda: self.kite.modify_order(
                variety=self.kite.VARIETY_REGULAR,
                order_id=stop_loss_order_id,
                exchange=exchange,
                tradingsymbol=trading_symbol,
                quantity=quantity,
                order_type=self.kite.ORDER_TYPE_SLM,
                trigger_price=rounded_stop_loss,
                product=product_to_use,
                validity=self.kite.VALIDITY_DAY,
            ),
            action_name=f"trail stop loss for {trading_symbol}",
        )
        logger.info("[LIVE] Trailed stop loss %s to %.2f", trading_symbol, rounded_stop_loss)
        return rounded_stop_loss

    def _simulate_market_buy(
        self,
        trading_symbol: str,
        exchange: str,
        last_price: float,
        quantity: int,
        stop_loss_price: float,
        product: str,
    ) -> ManagedOrder:
        entry_order_id = self._next_paper_order_id("ENTRY")
        stop_loss_order_id = self._next_paper_order_id("SL")

        self._paper_orders[entry_order_id] = {
            "order_id": entry_order_id,
            "status": "COMPLETE",
            "average_price": last_price,
            "price": last_price,
            "tradingsymbol": trading_symbol,
            "exchange": exchange,
            "transaction_type": "BUY",
            "product": product,
            "quantity": quantity,
        }
        self._paper_orders[stop_loss_order_id] = {
            "order_id": stop_loss_order_id,
            "status": "TRIGGER PENDING",
            "average_price": 0.0,
            "price": stop_loss_price,
            "trigger_price": stop_loss_price,
            "tradingsymbol": trading_symbol,
            "exchange": exchange,
            "transaction_type": "SELL",
            "product": product,
            "quantity": quantity,
        }

        self.trade_manager.record_trade(
            mode="PAPER",
            symbol=trading_symbol,
            side="BUY",
            quantity=quantity,
            price=last_price,
            status="SIMULATED",
        )
        logger.info("[PAPER] Simulated BUY %s @ %.2f", trading_symbol, last_price)

        return ManagedOrder(
            trading_symbol=trading_symbol,
            exchange=exchange,
            quantity=quantity,
            entry_order_id=entry_order_id,
            stop_loss_order_id=stop_loss_order_id,
            entry_price=last_price,
            stop_loss_price=stop_loss_price,
            status="COMPLETE",
        )

    def _next_paper_order_id(self, prefix: str) -> str:
        self._paper_order_count += 1
        return f"PAPER-{prefix}-{self._paper_order_count:06d}"

    def _calculate_stop_loss_price(self, entry_price: float, percent: float | None = None) -> float:
        stop_loss_percent = percent if percent is not None else self.settings.stop_loss_percent
        raw_price = entry_price * (1 - stop_loss_percent)
        tick_size = self.instrument.tick_size if self.instrument is not None else 0.05
        return self._round_to_tick(raw_price, tick_size=tick_size)

    def _ensure_live_ready(self) -> None:
        if self.mode != "LIVE":
            return
        if self.kite is None:
            raise RuntimeError("Kite client is required in LIVE mode")

    @staticmethod
    def _extract_average_price(order: dict[str, Any], fallback_price: float) -> float:
        average_price = order.get("average_price") or order.get("price") or fallback_price
        return float(average_price)

    @staticmethod
    def _round_to_tick(price: float, tick_size: float = 0.05) -> float:
        return round(round(price / tick_size) * tick_size, 2)

    def _with_retry(self, operation: Any, action_name: str) -> Any:
        last_error: Exception | None = None

        for attempt in range(1, self.settings.max_retries + 1):
            try:
                return operation()
            except Exception as exc:  # pragma: no cover - broker/network failure path
                last_error = exc
                logger.warning(
                    "%s failed on attempt %s/%s: %s",
                    action_name,
                    attempt,
                    self.settings.max_retries,
                    exc,
                )
                if attempt == self.settings.max_retries:
                    break
                time.sleep(self.settings.retry_delay_seconds)

        raise RuntimeError(f"Unable to {action_name} after retries") from last_error


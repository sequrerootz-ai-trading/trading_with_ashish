from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import mean, pstdev
from typing import Callable

from data.candle_store import Candle


@dataclass(frozen=True)
class BacktestTrade:
    side: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    exit_reason: str


@dataclass(frozen=True)
class BacktestMetrics:
    trades: int
    win_rate: float
    profit_factor: float
    max_drawdown: float
    sharpe_ratio: float
    net_pnl: float


@dataclass(frozen=True)
class BacktestResult:
    metrics: BacktestMetrics
    trade_log: list[BacktestTrade]


class Backtester:
    def __init__(self, brokerage_per_trade: float = 40.0, slippage_pct: float = 0.01) -> None:
        self.brokerage_per_trade = brokerage_per_trade
        self.slippage_pct = slippage_pct

    def run(
        self,
        candles: list[Candle],
        signal_fn: Callable[[list[Candle], int], tuple[str, float | None, float | None]],
        lot_size: int = 1,
    ) -> BacktestResult:
        balance_curve = [0.0]
        trade_log: list[BacktestTrade] = []
        open_trade: dict | None = None

        for index in range(21, len(candles)):
            signal, entry_ref, stop_loss = signal_fn(candles, index)
            candle = candles[index]
            if open_trade is None and signal in {"BUY_CE", "BUY_PE", "BUY", "SELL"} and entry_ref and stop_loss:
                side = "LONG" if signal in {"BUY_CE", "BUY", "BUY_PE"} else "SHORT"
                entry_price = candle.close * (1 + self.slippage_pct)
                open_trade = {
                    "side": side,
                    "entry_time": candle.end.isoformat(),
                    "entry_price": entry_price,
                    "stop_loss": stop_loss,
                    "quantity": lot_size,
                }
                continue

            if open_trade is None:
                continue

            exit_reason = None
            exit_price = candle.close
            if candle.low <= open_trade["stop_loss"] <= candle.high:
                exit_reason = "stop_loss"
                exit_price = open_trade["stop_loss"] * (1 - self.slippage_pct)
            elif index == len(candles) - 1:
                exit_reason = "session_close"
                exit_price = candle.close * (1 - self.slippage_pct)

            if exit_reason is None:
                continue

            gross_pnl = (exit_price - open_trade["entry_price"]) * open_trade["quantity"]
            net_pnl = gross_pnl - self.brokerage_per_trade
            trade_log.append(
                BacktestTrade(
                    side=open_trade["side"],
                    entry_time=open_trade["entry_time"],
                    exit_time=candle.end.isoformat(),
                    entry_price=round(open_trade["entry_price"], 2),
                    exit_price=round(exit_price, 2),
                    quantity=open_trade["quantity"],
                    pnl=round(net_pnl, 2),
                    exit_reason=exit_reason,
                )
            )
            balance_curve.append(balance_curve[-1] + net_pnl)
            open_trade = None

        metrics = _build_metrics(trade_log, balance_curve)
        return BacktestResult(metrics=metrics, trade_log=trade_log)


def _build_metrics(trade_log: list[BacktestTrade], balance_curve: list[float]) -> BacktestMetrics:
    wins = [trade.pnl for trade in trade_log if trade.pnl > 0]
    losses = [abs(trade.pnl) for trade in trade_log if trade.pnl < 0]
    returns = [trade.pnl for trade in trade_log]
    peak = balance_curve[0] if balance_curve else 0.0
    max_drawdown = 0.0
    for point in balance_curve:
        peak = max(peak, point)
        max_drawdown = max(max_drawdown, peak - point)

    win_rate = (len(wins) / len(trade_log) * 100.0) if trade_log else 0.0
    profit_factor = (sum(wins) / sum(losses)) if losses else float("inf") if wins else 0.0
    sharpe_ratio = 0.0
    if len(returns) > 1 and pstdev(returns) > 0:
        sharpe_ratio = (mean(returns) / pstdev(returns)) * math.sqrt(len(returns))

    return BacktestMetrics(
        trades=len(trade_log),
        win_rate=round(win_rate, 2),
        profit_factor=round(profit_factor, 2) if math.isfinite(profit_factor) else profit_factor,
        max_drawdown=round(max_drawdown, 2),
        sharpe_ratio=round(sharpe_ratio, 2),
        net_pnl=round(sum(returns), 2),
    )

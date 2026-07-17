"""Portfolio accounting — inventory (I_t), cash, fees, and realised P&L.

This is the terminal-wealth ledger the whole MDP is trying to maximise. It is
the single source of truth for KPIs (net spread, turnover, drawdown) computed in
the CLI summary.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .types import Fill, Position


@dataclass
class RoundTrip:
    """A matched buy→sell pair, used to measure net spread per round-trip."""

    key: str
    buy_price: float   # gross
    sell_price: float  # gross
    fees: float
    hold_ticks: int

    @property
    def net_margin(self) -> float:
        """Net profit / buy notional after all fees (the ≥8% KPI)."""
        return (self.sell_price - self.buy_price - self.fees) / self.buy_price


class Portfolio:
    def __init__(self, starting_cash: float = 0.0) -> None:
        self.starting_cash: float = starting_cash
        self.cash: float = starting_cash
        self.positions: dict[str, Position] = {}
        self.fills: list[Fill] = []
        self.round_trips: list[RoundTrip] = []
        self.realised_pnl: float = 0.0
        self._acquired_tick: dict[str, list[int]] = {}  # FIFO buy ticks per SKU

    def position(self, key: str) -> Position:
        return self.positions.setdefault(key, Position(key=key))

    def apply(self, fill: Fill) -> None:
        self.fills.append(fill)
        pos = self.position(fill.key)
        if fill.side == "buy":
            self.cash -= fill.price + fill.fee
            pos.qty += 1
            pos.cost_basis += fill.price + fill.fee
            self._acquired_tick.setdefault(fill.key, []).append(fill.tick)
        else:  # sell
            self.cash += fill.price - fill.fee
            if pos.qty > 0:
                unit_cost = pos.avg_cost
                pos.cost_basis -= unit_cost
                pos.qty -= 1
                self.realised_pnl += (fill.price - fill.fee) - unit_cost
                buy_tick = self._acquired_tick.get(fill.key, [fill.tick]).pop(0) \
                    if self._acquired_tick.get(fill.key) else fill.tick
                self.round_trips.append(
                    RoundTrip(
                        key=fill.key,
                        buy_price=unit_cost,     # cost basis already includes buy fee
                        sell_price=fill.price,
                        fees=fill.fee,
                        hold_ticks=fill.tick - buy_tick,
                    )
                )

    def inventory_value(self, mids: dict[str, float]) -> float:
        return sum(p.qty * mids.get(k, p.avg_cost) for k, p in self.positions.items())

    def equity(self, mids: dict[str, float]) -> float:
        """Mark-to-market terminal wealth: cash + inventory at current mids."""
        return self.cash + self.inventory_value(mids)

    @property
    def total_units_held(self) -> int:
        return sum(p.qty for p in self.positions.values())

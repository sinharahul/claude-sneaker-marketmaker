"""Backtester — runs the Phase-2 engine over recorded history + the arb signal.

It reuses every Phase-2 component (Pipeline, AvellanedaStoikovEngine, RiskManager,
HedgingPolicy, PaperExecutor, Portfolio) but drives its own tick loop so it can
add the Phase-3 cross-platform arbitrage pass (ACCUMULATE) WITHOUT touching
`smm.app`. Arb round-trips are booked to a separate ledger so they don't distort
the market-making holding-time KPI.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from smm.config import Config
from smm.data.pipeline import Pipeline
from smm.engine.hedging import HedgingPolicy
from smm.engine.pricing import AvellanedaStoikovEngine
from smm.engine.risk import RiskManager
from smm.execution.router import PaperExecutor
from smm.portfolio import Portfolio

from .arbitrage import ArbDetector, ArbOpportunity
from .replay import ReplaySource


@dataclass
class BacktestTick:
    tick: int
    equity: float
    peak_equity: float
    units_held: int
    fills: int
    hedges: int
    arbs: int
    halted: bool


@dataclass
class BacktestResult:
    history: list[BacktestTick]
    portfolio: Portfolio
    arb_profit: float
    arb_count: int
    arbs_seen: list[ArbOpportunity] = field(default_factory=list)


class Backtester:
    def __init__(self, cfg: Config, source: ReplaySource) -> None:
        self.cfg = cfg
        self.source = source
        self.pipeline = Pipeline(cfg)
        self.pricing = AvellanedaStoikovEngine(cfg.pricing)
        self.risk = RiskManager(cfg.risk)
        self.hedging = HedgingPolicy(cfg)
        self.executor = PaperExecutor(cfg, random.Random(cfg.run.seed + 1))
        self.arb = ArbDetector(cfg.fees)
        self.portfolio = Portfolio(cfg.run.starting_cash)
        self.peak_equity = cfg.run.starting_cash

    def run(self, steps: int | None = None) -> BacktestResult:
        n = self.source.num_ticks if steps is None else min(steps, self.source.num_ticks)
        cap = self.cfg.risk.max_inventory_per_sku
        history: list[BacktestTick] = []
        arb_profit = 0.0
        arb_count = 0
        arbs_seen: list[ArbOpportunity] = []

        for t in range(n):
            snaps = self.source.tick(t)
            refs = self.pipeline.process(snaps)
            fills = hedges = tick_arbs = 0

            for key, ref in refs.items():
                pos = self.portfolio.position(key)

                # --- market-making quote (identical to Phase 2) ---
                decision = self.risk.evaluate(ref, pos)
                quote = self.pricing.quote(
                    ref, pos.qty, cap,
                    suppress_bid=decision.suppress_bid,
                    suppress_ask=decision.suppress_ask,
                )
                for f in self.executor.try_quote(quote, pos, t):
                    self.portfolio.apply(f)
                    fills += 1

                # --- Phase-3 cross-platform arbitrage pass (ACCUMULATE) ---
                if not self.risk.halted:
                    opp = self.arb.scan(snaps[key])
                    if opp is not None and opp.profitable:
                        arb_profit += opp.net_edge
                        arb_count += 1
                        tick_arbs += 1
                        arbs_seen.append(opp)
                        # Self-liquidating paired trade: net cash, no inventory left.
                        self.portfolio.cash += opp.net_edge
                        self.portfolio.realised_pnl += opp.net_edge

                # --- hedging / liquidation (identical to Phase 2) ---
                action = self.hedging.decide(ref, self.portfolio.position(key), snaps[key])
                hedge_fill = self.executor.execute_hedge(action, snaps[key], t)
                if hedge_fill is not None and self.portfolio.position(key).qty > 0:
                    self.portfolio.apply(hedge_fill)
                    hedges += 1

            mids = {k: r.mid for k, r in refs.items()}
            equity = self.portfolio.equity(mids)
            self.peak_equity = max(self.peak_equity, equity)
            self.risk.update_drawdown(equity, self.peak_equity)
            history.append(BacktestTick(
                tick=t, equity=equity, peak_equity=self.peak_equity,
                units_held=self.portfolio.total_units_held,
                fills=fills, hedges=hedges, arbs=tick_arbs, halted=self.risk.halted,
            ))

        return BacktestResult(history, self.portfolio, arb_profit, arb_count, arbs_seen)

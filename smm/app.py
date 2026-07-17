"""Orchestrator: wires the four modules into one quote → fill → hedge loop.

Each tick, per SKU:
    Data Engine      ingest snapshot -> RefPrice (mid, σ)
    Risk             evaluate breakers/caps -> which sides to suppress
    Quoting Engine   Avellaneda–Stoikov quote given inventory
    Executor         simulate fills (paper)
    Hedging          decide + execute H_t liquidation
    Portfolio        book fills, mark equity, track drawdown
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .config import Config
from .data.pipeline import Pipeline
from .data.sources import MarketDataSource, build_source
from .engine.hedging import HedgingPolicy
from .engine.pricing import AvellanedaStoikovEngine
from .engine.risk import RiskManager
from .execution.router import Executor, PaperExecutor
from .portfolio import Portfolio


@dataclass
class TickReport:
    tick: int
    equity: float
    peak_equity: float
    units_held: int
    fills: int
    hedges: int
    halted: bool
    suppressions: list[str] = field(default_factory=list)


class MarketMaker:
    def __init__(
        self,
        cfg: Config,
        *,
        source: MarketDataSource | None = None,
        executor: Executor | None = None,
    ) -> None:
        self.cfg = cfg
        self._rng = random.Random(cfg.run.seed)
        # Separate RNG streams so swapping the source doesn't shift fill draws.
        self.source = source or build_source("mock", cfg, random.Random(cfg.run.seed))
        self.executor = executor or PaperExecutor(cfg, random.Random(cfg.run.seed + 1))
        self.pipeline = Pipeline(cfg)
        self.pricing = AvellanedaStoikovEngine(cfg.pricing)
        self.risk = RiskManager(cfg.risk)
        self.hedging = HedgingPolicy(cfg)
        self.portfolio = Portfolio(cfg.run.starting_cash)
        self.peak_equity = cfg.run.starting_cash
        self.history: list[TickReport] = []

    def run(self, steps: int | None = None) -> list[TickReport]:
        n = steps if steps is not None else self.cfg.run.steps
        cap = self.cfg.risk.max_inventory_per_sku
        for t in range(n):
            snaps = self.source.tick(t)
            refs = self.pipeline.process(snaps)

            fills = hedges = 0
            suppressions: list[str] = []
            for key, ref in refs.items():
                pos = self.portfolio.position(key)

                decision = self.risk.evaluate(ref, pos)
                if decision.reasons:
                    suppressions.extend(f"{key}: {r}" for r in decision.reasons)

                quote = self.pricing.quote(
                    ref, pos.qty, cap,
                    suppress_bid=decision.suppress_bid,
                    suppress_ask=decision.suppress_ask,
                )
                for f in self.executor.try_quote(quote, pos, t):
                    self.portfolio.apply(f)
                    fills += 1

                action = self.hedging.decide(ref, self.portfolio.position(key), snaps[key])
                hedge_fill = self.executor.execute_hedge(action, snaps[key], t)
                if hedge_fill is not None and self.portfolio.position(key).qty > 0:
                    self.portfolio.apply(hedge_fill)
                    hedges += 1

            mids = {k: r.mid for k, r in refs.items()}
            equity = self.portfolio.equity(mids)
            self.peak_equity = max(self.peak_equity, equity)
            self.risk.update_drawdown(equity, self.peak_equity)

            report = TickReport(
                tick=t,
                equity=equity,
                peak_equity=self.peak_equity,
                units_held=self.portfolio.total_units_held,
                fills=fills,
                hedges=hedges,
                halted=self.risk.halted,
                suppressions=suppressions,
            )
            self.history.append(report)
        return self.history

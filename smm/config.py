"""Typed configuration loaded from a TOML file (stdlib tomllib).

Keeping config in one immutable object means the engine takes no free
parameters — everything the algorithm needs is injected at construction time,
which is what makes paper runs reproducible and the model auditable.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from .types import SkuSpec


@dataclass(frozen=True)
class RunCfg:
    steps: int
    tick_hours: float
    seed: int
    starting_cash: float


@dataclass(frozen=True)
class PricingCfg:
    gamma: float
    kappa: float
    horizon_days: float
    min_half_pct: float
    capacity_widen: float


@dataclass(frozen=True)
class FillsCfg:
    base_intensity: float


@dataclass(frozen=True)
class FeesCfg:
    sell_fee_pct: float
    buy_fee_pct: float
    instant_payout_penalty: float


@dataclass(frozen=True)
class RiskCfg:
    max_inventory_per_sku: int
    vol_breaker_sigma: float
    vol_breaker_window: int
    max_drawdown_halt_pct: float


@dataclass(frozen=True)
class HedgingCfg:
    daily_decay_pct: float
    hedge_soft_ratio: float


@dataclass(frozen=True)
class Config:
    run: RunCfg
    pricing: PricingCfg
    fills: FillsCfg
    fees: FeesCfg
    risk: RiskCfg
    hedging: HedgingCfg
    universe: tuple[SkuSpec, ...]

    @staticmethod
    def load(path: str | Path) -> "Config":
        raw = tomllib.loads(Path(path).read_text())
        universe = tuple(
            SkuSpec(
                model=u["model"],
                size=str(u["size"]),
                seed_mid=float(u["mid"]),
                sigma=float(u["sigma"]),
            )
            for u in raw["universe"]
        )
        if not universe:
            raise ValueError("config 'universe' must list at least one SKU")
        return Config(
            run=RunCfg(**raw["run"]),
            pricing=PricingCfg(**raw["pricing"]),
            fills=FillsCfg(**raw["fills"]),
            fees=FeesCfg(**raw["fees"]),
            risk=RiskCfg(**raw["risk"]),
            hedging=HedgingCfg(**raw["hedging"]),
            universe=universe,
        )

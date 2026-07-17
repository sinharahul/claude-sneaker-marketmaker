"""Risk circuit breakers and structural caps (PRD §5, non-functional).

These sit between the pricing engine and execution and can veto either side of a
quote. They are deliberately simple, deterministic, and side-effect free so the
reason a quote was suppressed is always auditable.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import RiskCfg
from ..types import Position, RefPrice


@dataclass
class RiskDecision:
    suppress_bid: bool
    suppress_ask: bool
    reasons: tuple[str, ...] = ()


class RiskManager:
    def __init__(self, cfg: RiskCfg) -> None:
        self._c = cfg
        self._halted = False  # global drawdown kill-switch (latches on)

    @property
    def halted(self) -> bool:
        return self._halted

    def update_drawdown(self, equity: float, peak_equity: float) -> None:
        """Latch the global kill-switch if equity draws down past the limit."""
        if peak_equity > 0 and (peak_equity - equity) / peak_equity >= self._c.max_drawdown_halt_pct:
            self._halted = True

    def evaluate(self, ref: RefPrice, pos: Position) -> RiskDecision:
        reasons: list[str] = []
        suppress_bid = False
        suppress_ask = False

        if self._halted:
            return RiskDecision(True, True, ("global drawdown kill-switch active",))

        # Inventory cap (FR / §5): stop buying at the structural limit.
        if pos.qty >= self._c.max_inventory_per_sku:
            suppress_bid = True
            reasons.append(f"at inventory cap ({pos.qty}/{self._c.max_inventory_per_sku})")

        # Volatility breaker: halt bids during restock-driven vol spikes so we
        # don't accumulate into a falling market. Baseline = longer-window vol.
        baseline = max(ref.sigma_7d, 1e-9)
        if ref.sigma_short >= self._c.vol_breaker_sigma * baseline and ref.sigma_7d > 0:
            suppress_bid = True
            reasons.append(
                f"vol breaker: short σ {ref.sigma_short:.3f} ≥ "
                f"{self._c.vol_breaker_sigma}× baseline {baseline:.3f}"
            )

        return RiskDecision(suppress_bid, suppress_ask, tuple(reasons))

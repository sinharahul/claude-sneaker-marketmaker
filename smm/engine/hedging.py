"""Cross-platform hedging / liquidation policy — the H_t action (Module 3 brain).

This is the only genuinely dynamic-programming-flavoured decision left after the
closed-form quoting: a small per-SKU comparison of *expected carry value* vs.
*cost to shed now*. It answers FR-3.2 — dump inventory via instant cash-out when
expected depreciation outruns the spread the maker can still earn by holding.

Decision (per held SKU):
    risk_cost   = γ·σ$²·horizon·qty                          # marginal inventory-variance penalty (same term as pricing)
    hold_value  = P·(1 − decay·horizon) − risk_cost          # carry value NET of the risk of holding q units
    payout_now  = best_bid·(1 − instant_penalty)             # GOAT-style instant cash-out, net of haircut
    LIQUIDATE if we are over the soft capacity ratio AND the instant payout beats
    the risk-adjusted hold value; otherwise HOLD. Tying the threshold to γ·σ² is
    what makes fire-sales fire when inventory risk (not just depreciation) is the
    binding cost. ACCUMULATE is reserved for a future cross-platform arb signal
    and is not emitted by this baseline policy.
"""

from __future__ import annotations

from ..config import Config
from ..types import Hedge, HedgeAction, MarketSnapshot, Position, RefPrice


class HedgingPolicy:
    def __init__(self, cfg: Config) -> None:
        self._decay = cfg.hedging.daily_decay_pct
        self._horizon = cfg.pricing.horizon_days
        self._soft_ratio = cfg.hedging.hedge_soft_ratio
        self._cap = cfg.risk.max_inventory_per_sku
        self._instant_penalty = cfg.fees.instant_payout_penalty
        self._gamma = cfg.pricing.gamma  # shared risk-aversion term with the pricing core

    def decide(self, ref: RefPrice, pos: Position, snap: MarketSnapshot) -> HedgeAction:
        if pos.qty <= 0:
            return HedgeAction(ref.key, Hedge.HOLD, "no inventory")

        over_soft = pos.qty >= self._soft_ratio * self._cap
        best_bid = _best_bid(snap)
        if best_bid is None:
            return HedgeAction(ref.key, Hedge.HOLD, "no bid to hedge into")

        sigma_frac = ref.sigma_24h if ref.sigma_24h > 0 else ref.sigma_short
        sigma_d = sigma_frac * ref.mid
        risk_cost = self._gamma * sigma_d**2 * self._horizon * pos.qty
        expected_hold = ref.mid * (1.0 - self._decay * self._horizon) - risk_cost
        liquidate_now = best_bid * (1.0 - self._instant_penalty)

        if over_soft and liquidate_now >= expected_hold:
            return HedgeAction(
                ref.key,
                Hedge.LIQUIDATE,
                f"instant payout {liquidate_now:.0f} ≥ risk-adj hold {expected_hold:.0f} "
                f"(risk cost {risk_cost:.0f}) and over soft cap ({pos.qty}/{self._cap})",
            )
        return HedgeAction(ref.key, Hedge.HOLD, "carry value intact")


def _best_bid(snap: MarketSnapshot) -> float | None:
    bids = [b for b, _ in snap.books.values() if b is not None]
    return max(bids) if bids else None

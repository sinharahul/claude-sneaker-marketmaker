"""The quoting core — closed-form Avellaneda–Stoikov optimal market making.

WHY NOT TABULAR BELLMAN?
The PRD frames quoting as a Bellman value function V(I, P) over a continuous
state. Solving that by tabular dynamic programming across 5,000 SKUs × a
continuous (P, sigma, Δt) grid is a curse-of-dimensionality trap. Avellaneda &
Stoikov (2008) derive the *closed-form* optimum for exactly this reward —
spread capture minus an inventory-variance penalty γ·I²·σ² — so each SKU's
quotes are O(1) arithmetic. This module is that closed form; the only genuine
DP left is the tiny discrete hedge decision in `hedging.py`.

    reservation price   r = P − q·γ·σ²·(T−t)
    optimal total spread  = γ·σ²·(T−t) + (2/γ)·ln(1 + γ/κ)
    bid = r − spread/2,  ask = r + spread/2

Inventory skew is automatic: long inventory (q>0) pushes r below the mid, so the
ask becomes aggressive (clears inventory) and the bid backs off (blocks buys) —
exactly FR-2.2. Rising σ or utilisation widens the spread — FR-2.1.

σ here is a *dollar* volatility (fractional σ × mid) so γ and κ carry units 1/$.
"""

from __future__ import annotations

import math

from ..config import PricingCfg
from ..types import Quote, RefPrice


class AvellanedaStoikovEngine:
    def __init__(self, cfg: PricingCfg) -> None:
        self._c = cfg

    def quote(
        self,
        ref: RefPrice,
        inventory: int,
        capacity: int,
        *,
        suppress_bid: bool = False,
        suppress_ask: bool = False,
    ) -> Quote:
        """Produce the optimal two-sided quote for one SKU.

        `suppress_bid`/`suppress_ask` let risk controls veto a side (e.g. the
        volatility circuit breaker halts bids; a full book halts asks) without
        the pricing math needing to know why.
        """
        c = self._c
        mid = ref.mid
        # Use the medium-horizon vol as the risk estimate; convert to $ terms.
        sigma_frac = ref.sigma_24h if ref.sigma_24h > 0 else ref.sigma_short
        sigma_d = sigma_frac * mid
        horizon = c.horizon_days  # (T − t): steady-state planning horizon in days

        # --- Avellaneda–Stoikov closed form -------------------------------
        inv_skew = c.gamma * sigma_d**2 * horizon * inventory
        reservation = mid - inv_skew

        risk_half = 0.5 * c.gamma * sigma_d**2 * horizon
        adverse_half = (1.0 / c.gamma) * math.log1p(c.gamma / c.kappa)
        half = risk_half + adverse_half

        # FR-2.1: widen as the book fills up (utilisation ∈ [0,1]).
        util = min(1.0, abs(inventory) / capacity) if capacity else 0.0
        half *= 1.0 + (c.capacity_widen - 1.0) * util

        # Never quote inside the configured minimum half-spread.
        half = max(half, c.min_half_pct * mid)

        bid_price = reservation - half
        ask_price = reservation + half

        note = _describe(inventory)
        if suppress_bid or bid_price <= 0:
            bid_price = None
        if suppress_ask:
            ask_price = None

        return Quote(
            key=ref.key,
            mid=mid,
            reservation=reservation,
            bid_price=round(bid_price, 2) if bid_price is not None else None,
            ask_price=round(ask_price, 2) if ask_price is not None else None,
            bid_delta=round(mid - bid_price, 2) if bid_price is not None else None,
            ask_delta=round(ask_price - mid, 2) if ask_price is not None else None,
            note=note,
        )


def _describe(inventory: int) -> str:
    if inventory > 0:
        return "long: quotes skewed down (ask aggressive, bid defensive)"
    if inventory < 0:
        return "short: quotes skewed up (bid aggressive)"
    return "flat: symmetric spread, max capture"

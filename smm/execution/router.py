"""Order routing / execution.

`PaperExecutor` simulates whether a resting quote gets hit within a tick using
the same fill-intensity model the pricing core assumes: a marketable counter-
order arrives with probability 1 − exp(−λ·dt), where λ(δ) = A·exp(−κ·δ). Tighter
quotes (small δ) fill more often; wide quotes rarely do — this is the mechanism
that couples the spread choice to realised turnover, so paper KPIs are honest.

`LiveExecutor` is a stub: real order placement is out of scope for the skeleton.
"""

from __future__ import annotations

import math
import random
from abc import ABC, abstractmethod

from ..config import Config
from ..types import Fill, HedgeAction, Hedge, MarketSnapshot, Position, Quote


class Executor(ABC):
    @abstractmethod
    def try_quote(self, quote: Quote, pos: Position, tick: int) -> list[Fill]:
        ...

    @abstractmethod
    def execute_hedge(self, action: HedgeAction, snap: MarketSnapshot, tick: int) -> Fill | None:
        ...


class PaperExecutor(Executor):
    def __init__(self, cfg: Config, rng: random.Random) -> None:
        self._rng = rng
        self._A = cfg.fills.base_intensity
        self._kappa = cfg.pricing.kappa
        self._dt = cfg.run.tick_hours / 24.0  # tick length in days, matches λ units
        self._sell_fee = cfg.fees.sell_fee_pct
        self._buy_fee = cfg.fees.buy_fee_pct
        self._instant_penalty = cfg.fees.instant_payout_penalty

    def _fill_prob(self, delta: float) -> float:
        lam = self._A * math.exp(-self._kappa * max(delta, 0.0))
        return 1.0 - math.exp(-lam * self._dt)

    def try_quote(self, quote: Quote, pos: Position, tick: int) -> list[Fill]:
        fills: list[Fill] = []
        # Ask side: a buyer lifts our ask -> we sell one unit (only if we hold it).
        if quote.ask_price is not None and quote.ask_delta is not None and pos.qty > 0:
            if self._rng.random() < self._fill_prob(quote.ask_delta):
                fills.append(
                    Fill(quote.key, tick, "sell", quote.ask_price,
                         fee=quote.ask_price * self._sell_fee)
                )
        # Bid side: a seller hits our bid -> we buy one unit.
        if quote.bid_price is not None and quote.bid_delta is not None:
            if self._rng.random() < self._fill_prob(quote.bid_delta):
                fills.append(
                    Fill(quote.key, tick, "buy", quote.bid_price,
                         fee=quote.bid_price * self._buy_fee)
                )
        return fills

    def execute_hedge(self, action: HedgeAction, snap: MarketSnapshot, tick: int) -> Fill | None:
        if action.action is not Hedge.LIQUIDATE:
            return None
        bids = [b for b, _ in snap.books.values() if b is not None]
        if not bids:
            return None
        best_bid = max(bids)
        payout = best_bid * (1.0 - self._instant_penalty)  # instant cash-out haircut
        return Fill(action.key, tick, "sell", payout,
                    fee=payout * self._sell_fee, is_hedge=True)


class LiveExecutor(Executor):
    """Placeholder. Real execution is operator-run, not automated by the skeleton."""

    def try_quote(self, quote: Quote, pos: Position, tick: int) -> list[Fill]:  # pragma: no cover
        raise NotImplementedError(
            "Live order placement is intentionally not implemented — run execution "
            "under your own credentials with an explicit trigger."
        )

    def execute_hedge(self, action, snap, tick):  # pragma: no cover
        raise NotImplementedError("Live hedging is operator-run.")

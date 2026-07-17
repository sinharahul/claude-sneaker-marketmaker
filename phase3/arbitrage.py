"""Cross-platform arbitrage detector — the ACCUMULATE (H_t = +1) signal.

FR-3.1: monitor alternative platforms for arbitrage pathways. When the highest
bid on one venue exceeds the lowest ask on another by more than the round-trip
fee load, there is a buy-here / sell-there pathway. Given sneaker fees
(~13.5% round-trip in the default config), only sizeable dislocations clear the
hurdle — which is exactly why the PRD sees 10–30% spreads persist rather than
getting arbed away instantly. The detector reports both gross and net edge so
that reality is visible.
"""

from __future__ import annotations

from dataclasses import dataclass

from smm.config import FeesCfg
from smm.types import MarketSnapshot


@dataclass(frozen=True)
class ArbOpportunity:
    key: str
    buy_platform: str
    sell_platform: str
    buy_ask: float          # price we pay (lowest ask, gross)
    sell_bid: float         # price we receive (highest bid, gross)
    gross_edge: float       # sell_bid - buy_ask, in $
    net_edge: float         # after buy + sell fees, in $
    net_edge_pct: float     # net_edge / buy_ask

    @property
    def profitable(self) -> bool:
        return self.net_edge > 0


class ArbDetector:
    def __init__(self, fees: FeesCfg, min_edge_pct: float = 0.0) -> None:
        self._buy_fee = fees.buy_fee_pct
        self._sell_fee = fees.sell_fee_pct
        self._min = min_edge_pct

    def scan(self, snap: MarketSnapshot) -> ArbOpportunity | None:
        """Return the best profitable cross-platform pathway for one SKU, if any."""
        best_bid = best_bid_plat = None
        best_ask = best_ask_plat = None
        for platform, (bid, ask) in snap.books.items():
            if bid is not None and (best_bid is None or bid > best_bid):
                best_bid, best_bid_plat = bid, platform
            if ask is not None and (best_ask is None or ask < best_ask):
                best_ask, best_ask_plat = ask, platform

        if best_bid is None or best_ask is None or best_bid_plat == best_ask_plat:
            return None

        buy_cost = best_ask * (1.0 + self._buy_fee)
        sell_proceeds = best_bid * (1.0 - self._sell_fee)
        net = sell_proceeds - buy_cost
        opp = ArbOpportunity(
            key=snap.key,
            buy_platform=best_ask_plat,
            sell_platform=best_bid_plat,
            buy_ask=best_ask,
            sell_bid=best_bid,
            gross_edge=round(best_bid - best_ask, 2),
            net_edge=round(net, 2),
            net_edge_pct=net / best_ask if best_ask else 0.0,
        )
        return opp if opp.net_edge_pct > self._min else None

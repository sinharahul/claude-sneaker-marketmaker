"""Core domain types shared across the data, engine, and execution modules.

These map directly onto the PRD's mathematical framework (§3):
    MarketSnapshot / RefPrice  -> state components (P_t, sigma_t)
    Quote                      -> action components (delta_bid, delta_ask)
    HedgeAction                -> action component  (H_t)
    Position / Portfolio       -> inventory state   (I_t) and terminal wealth
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


def sku_key(model: str, size: str) -> str:
    """Canonical identifier for a SKU/size cell in the state space."""
    return f"{model}|{size}"


@dataclass(frozen=True)
class SkuSpec:
    """Static definition of one quotable SKU/size cell."""

    model: str
    size: str
    seed_mid: float
    sigma: float  # per-day fractional volatility used to seed the synthetic feed

    @property
    def key(self) -> str:
        return sku_key(self.model, self.size)


@dataclass
class MarketSnapshot:
    """Top-of-book observation for one SKU at one tick, across platforms.

    NOTE: real StockX/GOAT/eBay endpoints expose *top of book* (lowest ask,
    highest bid, last sale) rather than full L2 depth, so the state deliberately
    models best-bid/ask per platform rather than a synthetic order book.
    """

    key: str
    tick: int
    # platform -> (best_bid, best_ask). None where a side is empty.
    books: dict[str, tuple[float | None, float | None]] = field(default_factory=dict)
    last_sale: float | None = None


@dataclass
class RefPrice:
    """Derived reference state for a SKU (Data Engine output, FR-1.2 / FR-1.3)."""

    key: str
    tick: int
    mid: float            # P_t: VWAP synthetic mid across platforms
    sigma_short: float    # short-window fractional vol (breaker input)
    sigma_1h: float
    sigma_24h: float
    sigma_7d: float


@dataclass
class Quote:
    """Action output of the quoting engine for one SKU (delta_bid, delta_ask)."""

    key: str
    mid: float
    reservation: float          # A–S reservation price r_t
    bid_price: float | None     # None => not quoting this side (e.g. at cap / breaker)
    ask_price: float | None
    bid_delta: float | None     # P_t - bid_price
    ask_delta: float | None     # ask_price - P_t
    note: str = ""              # human-readable reason for asymmetry/suppression


class Hedge(IntEnum):
    """H_t ∈ {-1, 0, 1} from the action space."""

    LIQUIDATE = -1   # fire-sale / instant cash-out to shed inventory
    HOLD = 0
    ACCUMULATE = 1   # opportunistic cross-platform buy


@dataclass
class HedgeAction:
    key: str
    action: Hedge = Hedge.HOLD
    reason: str = ""


@dataclass
class Fill:
    """A completed round-leg from the (paper) executor."""

    key: str
    tick: int
    side: str          # "buy" | "sell"
    price: float       # gross execution price before fees
    fee: float
    is_hedge: bool = False


@dataclass
class Position:
    """Inventory state I_t for one SKU plus cost basis for P&L attribution."""

    key: str
    qty: int = 0
    cost_basis: float = 0.0   # total $ paid for the qty currently held (incl. buy fees)

    @property
    def avg_cost(self) -> float:
        return self.cost_basis / self.qty if self.qty else 0.0

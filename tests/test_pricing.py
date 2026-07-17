"""Behavioural tests for the pricing core and the end-to-end loop.

Run with:  python -m unittest discover -s tests
(zero third-party deps, so no pytest required).
"""

import unittest

from smm.config import Config
from smm.engine.pricing import AvellanedaStoikovEngine
from smm.engine.hedging import HedgingPolicy
from smm.app import MarketMaker
from smm.types import Hedge, MarketSnapshot, Position, RefPrice

CONFIG = "config/default.toml"


def _ref(mid=300.0, sigma=0.03):
    return RefPrice(key="X|10", tick=0, mid=mid, sigma_short=sigma,
                    sigma_1h=sigma, sigma_24h=sigma, sigma_7d=sigma)


class TestAvellanedaStoikov(unittest.TestCase):
    def setUp(self):
        self.cfg = Config.load(CONFIG)
        self.eng = AvellanedaStoikovEngine(self.cfg.pricing)
        self.cap = self.cfg.risk.max_inventory_per_sku

    def test_flat_inventory_is_symmetric(self):
        q = self.eng.quote(_ref(), inventory=0, capacity=self.cap)
        self.assertAlmostEqual(q.reservation, q.mid, places=6)
        self.assertAlmostEqual(q.bid_delta, q.ask_delta, places=6)

    def test_long_inventory_skews_reservation_below_mid(self):
        q = self.eng.quote(_ref(), inventory=10, capacity=self.cap)
        # Long => reservation below mid => ask pulled toward mid (aggressive sell).
        self.assertLess(q.reservation, q.mid)
        self.assertLess(q.ask_delta, q.bid_delta)

    def test_short_inventory_skews_reservation_above_mid(self):
        q = self.eng.quote(_ref(), inventory=-5, capacity=self.cap)
        self.assertGreater(q.reservation, q.mid)
        self.assertLess(q.bid_delta, q.ask_delta)  # bid aggressive to buy back

    def test_higher_vol_widens_spread(self):
        lo = self.eng.quote(_ref(sigma=0.02), 0, self.cap)
        hi = self.eng.quote(_ref(sigma=0.08), 0, self.cap)
        self.assertGreater(hi.ask_delta + hi.bid_delta, lo.ask_delta + lo.bid_delta)

    def test_capacity_widens_spread(self):
        near_flat = self.eng.quote(_ref(), 1, self.cap)
        near_cap = self.eng.quote(_ref(), self.cap, self.cap)
        # Total spread (2·half) must widen with utilisation (FR-2.1)...
        self.assertGreater(
            near_cap.ask_delta + near_cap.bid_delta,
            near_flat.ask_delta + near_flat.bid_delta,
        )
        # ...while the ask becomes strictly more aggressive to clear the book
        # (δ_ask can even go negative — willing to sell below mid at the cap).
        self.assertLess(near_cap.ask_delta, near_flat.ask_delta)

    def test_min_half_spread_floor(self):
        q = self.eng.quote(_ref(sigma=1e-6), 0, self.cap)
        floor = self.cfg.pricing.min_half_pct * q.mid
        self.assertGreaterEqual(q.ask_delta + 1e-9, floor)


class TestHedging(unittest.TestCase):
    def setUp(self):
        self.pol = HedgingPolicy(Config.load(CONFIG))
        self.snap = MarketSnapshot(
            "J|10", 0, books={"stockx": (292.0, 320.0), "goat": (290.0, 318.0)},
            last_sale=300.0,
        )
        self.ref = RefPrice("J|10", 0, mid=300.0, sigma_short=0.06,
                            sigma_1h=0.06, sigma_24h=0.06, sigma_7d=0.06)

    def test_liquidates_when_jammed_at_cap(self):
        pos = Position("J|10", qty=20, cost_basis=20 * 250)
        self.assertIs(self.pol.decide(self.ref, pos, self.snap).action, Hedge.LIQUIDATE)

    def test_holds_when_inventory_light(self):
        pos = Position("J|10", qty=1, cost_basis=250)
        self.assertIs(self.pol.decide(self.ref, pos, self.snap).action, Hedge.HOLD)

    def test_holds_when_flat(self):
        pos = Position("J|10", qty=0)
        self.assertIs(self.pol.decide(self.ref, pos, self.snap).action, Hedge.HOLD)


class TestEndToEnd(unittest.TestCase):
    def test_paper_run_is_deterministic_and_trades(self):
        cfg = Config.load(CONFIG)
        h1 = MarketMaker(cfg).run(120)
        h2 = MarketMaker(cfg).run(120)
        # Same seed => identical equity path (reproducibility).
        self.assertEqual([r.equity for r in h1], [r.equity for r in h2])
        # The loop should actually transact.
        self.assertGreater(sum(r.fills for r in h1), 0)


if __name__ == "__main__":
    unittest.main()

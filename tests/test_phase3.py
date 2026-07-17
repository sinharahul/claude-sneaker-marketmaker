"""Tests for the Phase-3 package. These must NOT affect Phase-2 behaviour.

Run:  python3 -m unittest discover -s tests
"""

import os
import tempfile
import unittest

from smm.config import Config
from smm.types import MarketSnapshot

from phase3.arbitrage import ArbDetector
from phase3.backtest import Backtester
from phase3.calibrate import demo_recovery, fit_intensity
from phase3.recorder import record
from phase3.replay import ReplaySource

CONFIG = "config/default.toml"


class TestReplayRoundTrip(unittest.TestCase):
    def test_record_then_replay_matches_shape(self):
        cfg = Config.load(CONFIG)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "h.csv")
            record(cfg, path, steps=50, seed=3)
            src = ReplaySource(path)
            self.assertEqual(src.num_ticks, 50)
            self.assertEqual(src.keys, {s.key for s in cfg.universe})
            snap = src.tick(0)[cfg.universe[0].key]
            self.assertIsInstance(snap, MarketSnapshot)
            # every platform book round-trips as a (bid, ask) pair
            for bid, ask in snap.books.values():
                self.assertIsNotNone(bid)
                self.assertIsNotNone(ask)


class TestCalibration(unittest.TestCase):
    def test_recovers_known_parameters(self):
        result, true_a, true_k = demo_recovery(true_A=4.0, true_kappa=0.06,
                                               dt=1.0, n=120_000, seed=11)
        self.assertLess(abs(result.A - true_a) / true_a, 0.15)
        self.assertLess(abs(result.kappa - true_k) / true_k, 0.15)
        self.assertGreater(result.r2, 0.9)

    def test_rejects_degenerate_input(self):
        with self.assertRaises(ValueError):
            fit_intensity([5.0] * 100, [True] * 100, dt=1.0)


class TestArbitrage(unittest.TestCase):
    def setUp(self):
        self.det = ArbDetector(Config.load(CONFIG).fees)

    def test_detects_planted_crossable_arb(self):
        # goat bid far above stockx ask -> buy stockx, sell goat
        snap = MarketSnapshot("X|10", 0, books={
            "stockx": (200.0, 210.0),
            "goat": (280.0, 300.0),
        })
        opp = self.det.scan(snap)
        self.assertIsNotNone(opp)
        self.assertEqual(opp.buy_platform, "stockx")
        self.assertEqual(opp.sell_platform, "goat")
        self.assertTrue(opp.profitable)

    def test_no_arb_when_books_aligned(self):
        snap = MarketSnapshot("X|10", 0, books={
            "stockx": (200.0, 210.0),
            "goat": (201.0, 211.0),
        })
        self.assertIsNone(self.det.scan(snap))


class TestBacktester(unittest.TestCase):
    def test_backtest_runs_and_books_arbs(self):
        cfg = Config.load(CONFIG)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "h.csv")
            # heavy dislocation so profitable arbs certainly appear
            record(cfg, path, steps=200, seed=5,
                   dislocation_prob=0.15, dislocation_size=0.35)
            res = Backtester(cfg, ReplaySource(path)).run()
            self.assertEqual(len(res.history), 200)
            self.assertGreater(res.arb_count, 0)
            self.assertGreater(sum(r.fills for r in res.history), 0)

    def test_backtest_is_deterministic(self):
        cfg = Config.load(CONFIG)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "h.csv")
            record(cfg, path, steps=120, seed=5)
            r1 = Backtester(cfg, ReplaySource(path)).run()
            r2 = Backtester(cfg, ReplaySource(path)).run()
            self.assertEqual([t.equity for t in r1.history],
                             [t.equity for t in r2.history])


if __name__ == "__main__":
    unittest.main()

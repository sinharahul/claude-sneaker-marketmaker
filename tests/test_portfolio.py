"""Unit tests for the portfolio ledger (`smm.portfolio`).

`Portfolio` is the terminal-wealth accounting the whole MDP maximises, and
`RoundTrip.net_margin` is the ≥8% net-spread KPI the README reports — but both
were previously only exercised indirectly by the end-to-end paper run. These
tests pin the arithmetic down directly: fee-inclusive cost basis, FIFO holding
period, realised P&L, and mark-to-market equity.

Deliberately dependency-free (stdlib + `smm.portfolio`/`smm.types` only), so it
runs even when the rest of the package cannot import.

Run with:  python -m unittest discover -s tests
"""

import unittest

from smm.portfolio import Portfolio, RoundTrip
from smm.types import Fill

KEY = "AJ1|10"


def _buy(price, fee, tick=0, key=KEY):
    return Fill(key, tick, "buy", price, fee=fee)


def _sell(price, fee, tick=0, key=KEY):
    return Fill(key, tick, "sell", price, fee=fee)


class TestRoundTripNetMargin(unittest.TestCase):
    """`net_margin` is a pure function of the four round-trip numbers."""

    def test_net_margin_is_profit_over_buy_notional(self):
        rt = RoundTrip(KEY, buy_price=206.0, sell_price=260.0, fees=27.3,
                       hold_ticks=5)
        # (260 − 206 − 27.3) / 206
        self.assertAlmostEqual(rt.net_margin, 26.7 / 206.0, places=9)

    def test_fees_can_flip_a_gross_win_into_a_net_loss(self):
        # +5% gross, but a 10.5% sell fee swamps it — the core economics the
        # README calls out (you need ~18–22% gross to clear the 8% KPI).
        rt = RoundTrip(KEY, buy_price=200.0, sell_price=210.0, fees=22.05,
                       hold_ticks=1)
        self.assertLess(rt.net_margin, 0.0)


class TestPortfolioAccounting(unittest.TestCase):
    def setUp(self):
        self.pf = Portfolio(starting_cash=10_000.0)

    def test_buy_debits_cash_and_capitalises_the_fee(self):
        self.pf.apply(_buy(200.0, fee=6.0, tick=0))

        pos = self.pf.position(KEY)
        self.assertEqual(pos.qty, 1)
        # Buy fee is capitalised into cost basis, not expensed separately.
        self.assertAlmostEqual(pos.cost_basis, 206.0, places=9)
        self.assertAlmostEqual(pos.avg_cost, 206.0, places=9)
        self.assertAlmostEqual(self.pf.cash, 10_000.0 - 206.0, places=9)
        self.assertAlmostEqual(self.pf.realised_pnl, 0.0, places=9)
        self.assertEqual(self.pf.round_trips, [])

    def test_round_trip_books_realised_pnl_net_of_both_fees(self):
        self.pf.apply(_buy(200.0, fee=6.0, tick=0))
        self.pf.apply(_sell(260.0, fee=27.3, tick=5))

        pos = self.pf.position(KEY)
        self.assertEqual(pos.qty, 0)
        self.assertAlmostEqual(pos.cost_basis, 0.0, places=9)
        # cash: 10000 − 206 + (260 − 27.3)
        self.assertAlmostEqual(self.pf.cash, 10_026.7, places=9)
        # realised: (260 − 27.3) − 206
        self.assertAlmostEqual(self.pf.realised_pnl, 26.7, places=9)

        self.assertEqual(len(self.pf.round_trips), 1)
        rt = self.pf.round_trips[0]
        self.assertAlmostEqual(rt.buy_price, 206.0, places=9)
        self.assertAlmostEqual(rt.sell_price, 260.0, places=9)
        self.assertEqual(rt.hold_ticks, 5)
        # Round-trip margin clears the 8% net-spread KPI.
        self.assertGreater(rt.net_margin, 0.08)

    def test_hold_ticks_uses_fifo_acquisition_order(self):
        self.pf.apply(_buy(200.0, fee=6.0, tick=0))
        self.pf.apply(_buy(220.0, fee=6.6, tick=3))
        self.pf.apply(_sell(260.0, fee=27.3, tick=10))

        rt = self.pf.round_trips[0]
        # FIFO: the sale is matched against the tick-0 lot, not the tick-3 one.
        self.assertEqual(rt.hold_ticks, 10)
        # ...while the cost relieved is the *average* cost of the two lots.
        self.assertAlmostEqual(rt.buy_price, (206.0 + 226.6) / 2, places=9)
        self.assertEqual(self.pf.position(KEY).qty, 1)

    def test_sell_without_inventory_credits_cash_but_books_no_round_trip(self):
        self.pf.apply(_sell(100.0, fee=5.0, tick=1))

        self.assertAlmostEqual(self.pf.cash, 10_095.0, places=9)
        self.assertEqual(self.pf.position(KEY).qty, 0)
        self.assertEqual(self.pf.round_trips, [])
        self.assertAlmostEqual(self.pf.realised_pnl, 0.0, places=9)

    def test_equity_marks_inventory_to_supplied_mids(self):
        self.pf.apply(_buy(200.0, fee=6.0, tick=0))

        # Marked up: cash (9794) + 1 unit @ 250.
        self.assertAlmostEqual(self.pf.equity({KEY: 250.0}), 10_044.0, places=9)
        # No mid available => fall back to cost basis, i.e. equity is unchanged.
        self.assertAlmostEqual(self.pf.equity({}), 10_000.0, places=9)

    def test_total_units_held_sums_across_skus(self):
        self.pf.apply(_buy(200.0, fee=6.0, tick=0))
        self.pf.apply(_buy(300.0, fee=9.0, tick=0, key="AJ1|11"))
        self.pf.apply(_sell(260.0, fee=27.3, tick=2))

        self.assertEqual(self.pf.total_units_held, 1)
        self.assertEqual(len(self.pf.fills), 3)


if __name__ == "__main__":
    unittest.main()

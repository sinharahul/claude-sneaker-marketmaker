"""Phase-3 CLI — record history, backtest, calibrate, and inspect arbs.

    python3 -m phase3.cli record   --out data/history.csv --steps 800 --dislocation-prob 0.03
    python3 -m phase3.cli backtest --history data/history.csv
    python3 -m phase3.cli calibrate --demo
    python3 -m phase3.cli arb-demo  --history data/history.csv

None of these touch a live platform; `record`/`backtest` run fully offline.
"""

from __future__ import annotations

import argparse
import statistics
import sys

from smm.config import Config

from .arbitrage import ArbDetector
from .backtest import Backtester
from .calibrate import demo_recovery, fit_intensity
from .recorder import record
from .replay import ReplaySource


def _cmd_record(a: argparse.Namespace) -> int:
    cfg = Config.load(a.config)
    path = record(cfg, a.out, steps=a.steps, seed=a.seed,
                  dislocation_prob=a.dislocation_prob,
                  dislocation_size=a.dislocation_size)
    src = ReplaySource(path)
    print(f"wrote {path}  ({src.num_ticks} ticks × {len(src.keys)} SKUs, "
          f"dislocation_prob={a.dislocation_prob})")
    return 0


def _cmd_backtest(a: argparse.Namespace) -> int:
    cfg = Config.load(a.config)
    src = ReplaySource(a.history)
    res = Backtester(cfg, src).run(a.steps)
    _print_backtest(cfg, res)
    return 0


def _cmd_calibrate(a: argparse.Namespace) -> int:
    if a.demo:
        result, true_a, true_k = demo_recovery(
            true_A=a.true_a, true_kappa=a.true_k, dt=a.dt, n=a.n, seed=a.seed)
        print("\nFill-intensity calibration — recovery test")
        print("-" * 52)
        print(f"  true   : A={true_a:.3f}  κ={true_k:.4f}")
        print(f"  fitted : {result.summary()}")
        errA = abs(result.A - true_a) / true_a
        errK = abs(result.kappa - true_k) / true_k
        print(f"  error  : A {errA:.1%}   κ {errK:.1%}")
        print()
        return 0
    if not a.fills:
        print("give --demo or --fills <csv>", file=sys.stderr)
        return 2
    deltas, filled = _load_fills(a.fills)
    result = fit_intensity(deltas, filled, a.dt)
    print(f"\nCalibrated from {a.fills}: {result.summary()}\n")
    return 0


def _cmd_arb(a: argparse.Namespace) -> int:
    cfg = Config.load(a.config)
    src = ReplaySource(a.history)
    det = ArbDetector(cfg.fees)
    n = src.num_ticks if a.steps is None else min(a.steps, src.num_ticks)
    found = []
    for t in range(n):
        for snap in src.tick(t).values():
            opp = det.scan(snap)
            if opp and opp.profitable:
                found.append((t, opp))
    print(f"\nProfitable cross-platform arbs: {len(found)} "
          f"over {n} ticks × {len(src.keys)} SKUs")
    for t, opp in found[:12]:
        print(f"  t={t:<4} {opp.key:<28} buy {opp.buy_platform}@{opp.buy_ask:.2f} "
              f"-> sell {opp.sell_platform}@{opp.sell_bid:.2f}  "
              f"net {opp.net_edge:+.2f} ({opp.net_edge_pct:+.1%})")
    if len(found) > 12:
        print(f"  ... and {len(found) - 12} more")
    print()
    return 0


def _print_backtest(cfg, res) -> None:
    pf = res.portfolio
    tick_days = cfg.run.tick_hours / 24.0
    final = res.history[-1]
    margins = [rt.net_margin for rt in pf.round_trips]
    holds = [rt.hold_ticks * tick_days for rt in pf.round_trips]
    avg_margin = statistics.mean(margins) if margins else 0.0
    avg_hold = statistics.mean(holds) if holds else 0.0
    max_dd = max((r.peak_equity - r.equity) / r.peak_equity
                 for r in res.history if r.peak_equity > 0)
    flag = lambda ok: "✅" if ok else "⚠️ "

    print("\n" + "=" * 60)
    print(" PHASE-3 BACKTEST — replay + cross-platform arbitrage")
    print("=" * 60)
    print(f" ticks replayed     : {len(res.history)}  ({len(res.history) * tick_days:.1f} sim-days)")
    print(f" market-making      : {len(pf.round_trips)} round-trips, "
          f"{sum(1 for f in pf.fills if f.is_hedge)} hedge liquidations")
    print(f" arbitrage          : {res.arb_count} executions, "
          f"${res.arb_profit:,.2f} net profit")
    print(f" realised P&L       : ${pf.realised_pnl:,.2f}  "
          f"(incl. arb ${res.arb_profit:,.2f})")
    print(f" ending equity      : ${final.equity:,.2f}  (peak ${final.peak_equity:,.2f})")
    print(f" kill-switch        : {'TRIPPED' if final.halted else 'clear'}")
    print("-" * 60)
    print(" KPIs (market-making round-trips only)")
    print(f"  {flag(avg_margin >= 0.08)} net spread / round-trip : {avg_margin:6.2%}   (target ≥ 8%)")
    print(f"  {flag(0 < avg_hold < 11)} avg holding time        : {avg_hold:6.2f}d  (target < 11d)")
    print(f"  {flag(max_dd < 0.05)} max drawdown            : {max_dd:6.2%}   (target < 5%)")
    print("=" * 60 + "\n")


def _load_fills(path: str):
    import csv
    deltas, filled = [], []
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            deltas.append(float(row["delta"]))
            filled.append(row["filled"].strip().lower() in ("1", "true", "yes"))
    return deltas, filled


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="phase3", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("record", help="record a replayable market-history CSV")
    r.add_argument("--config", default="config/default.toml")
    r.add_argument("--out", default="data/history.csv")
    r.add_argument("--steps", type=int, default=800)
    r.add_argument("--seed", type=int, default=None)
    r.add_argument("--dislocation-prob", type=float, default=0.03, dest="dislocation_prob")
    r.add_argument("--dislocation-size", type=float, default=0.22, dest="dislocation_size")
    r.set_defaults(func=_cmd_record)

    b = sub.add_parser("backtest", help="replay recorded history through the engine")
    b.add_argument("--config", default="config/default.toml")
    b.add_argument("--history", default="data/history.csv")
    b.add_argument("--steps", type=int, default=None)
    b.set_defaults(func=_cmd_backtest)

    c = sub.add_parser("calibrate", help="fit fill-intensity A, κ from data")
    c.add_argument("--demo", action="store_true", help="run a recovery test on synthetic fills")
    c.add_argument("--fills", default=None, help="CSV with columns delta,filled")
    c.add_argument("--dt", type=float, default=1.0)
    c.add_argument("--n", type=int, default=100_000)
    c.add_argument("--true-a", type=float, default=4.0, dest="true_a")
    c.add_argument("--true-k", type=float, default=0.06, dest="true_k")
    c.add_argument("--seed", type=int, default=7)
    c.set_defaults(func=_cmd_calibrate)

    q = sub.add_parser("arb-demo", help="list profitable cross-platform arbs in history")
    q.add_argument("--config", default="config/default.toml")
    q.add_argument("--history", default="data/history.csv")
    q.add_argument("--steps", type=int, default=None)
    q.set_defaults(func=_cmd_arb)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

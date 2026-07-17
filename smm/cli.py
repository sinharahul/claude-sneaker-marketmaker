"""Headless CLI entry point (PRD §6: config-driven, no manual trading UI).

    smm run   --config config/default.toml [--steps N] [--seed S]
    smm quote --config config/default.toml [--sku "Model|Size"]   # inspect the pricing core
"""

from __future__ import annotations

import argparse
import statistics
import sys

from .app import MarketMaker
from .config import Config
from .data.pipeline import Pipeline
from .engine.pricing import AvellanedaStoikovEngine


def _cmd_run(args: argparse.Namespace) -> int:
    cfg = Config.load(args.config)
    if args.steps is not None:
        cfg = _override(cfg, steps=args.steps)
    if args.seed is not None:
        cfg = _override(cfg, seed=args.seed)

    mm = MarketMaker(cfg)
    history = mm.run()
    _print_summary(cfg, mm, history)
    return 0


def _cmd_quote(args: argparse.Namespace) -> int:
    """Show how the A–S engine skews quotes as inventory changes — makes the
    core algorithm legible without running the whole loop."""
    cfg = Config.load(args.config)
    spec = cfg.universe[0]
    if args.sku:
        spec = next((s for s in cfg.universe if s.key == args.sku), spec)

    # Build a RefPrice at the seed mid with the SKU's configured vol.
    pipe = Pipeline(cfg)
    from .types import RefPrice  # local import: only needed here
    ref = RefPrice(
        key=spec.key, tick=0, mid=spec.seed_mid,
        sigma_short=spec.sigma, sigma_1h=spec.sigma,
        sigma_24h=spec.sigma, sigma_7d=spec.sigma,
    )
    eng = AvellanedaStoikovEngine(cfg.pricing)
    cap = cfg.risk.max_inventory_per_sku

    print(f"\nQuote sensitivity — {spec.model} (size {spec.size}), "
          f"mid ${spec.seed_mid:.0f}, σ={spec.sigma:.1%}/day, cap {cap}\n")
    print(f"{'inv':>4} {'bid':>9} {'ask':>9} {'reserv':>9} "
          f"{'δbid':>7} {'δask':>7}  note")
    print("-" * 78)
    for inv in (-5, 0, 5, 10, 15, 20):
        q = eng.quote(ref, inv, cap)
        bid = f"${q.bid_price:.2f}" if q.bid_price else "—"
        ask = f"${q.ask_price:.2f}" if q.ask_price else "—"
        db = f"{q.bid_delta:.2f}" if q.bid_delta is not None else "—"
        da = f"{q.ask_delta:.2f}" if q.ask_delta is not None else "—"
        print(f"{inv:>4} {bid:>9} {ask:>9} ${q.reservation:>8.2f} "
              f"{db:>7} {da:>7}  {q.note}")
    print()
    return 0


def _override(cfg: Config, *, steps: int | None = None, seed: int | None = None) -> Config:
    from dataclasses import replace
    run = cfg.run
    run = replace(run, steps=steps if steps is not None else run.steps,
                  seed=seed if seed is not None else run.seed)
    return replace(cfg, run=run)


def _print_summary(cfg: Config, mm: MarketMaker, history) -> None:
    pf = mm.portfolio
    tick_days = cfg.run.tick_hours / 24.0
    final = history[-1]

    # --- KPI 1: net spread per round-trip (target ≥ 8%) ---
    margins = [rt.net_margin for rt in pf.round_trips]
    avg_margin = statistics.mean(margins) if margins else 0.0

    # --- KPI 2: inventory turnover / holding time (target < 11 days) ---
    holds = [rt.hold_ticks * tick_days for rt in pf.round_trips]
    avg_hold = statistics.mean(holds) if holds else 0.0

    # --- KPI 3: max drawdown (target < 5%) ---
    max_dd = 0.0
    for r in history:
        if r.peak_equity > 0:
            max_dd = max(max_dd, (r.peak_equity - r.equity) / r.peak_equity)

    n_hedges = sum(1 for f in pf.fills if f.is_hedge)
    n_buys = sum(1 for f in pf.fills if f.side == "buy")
    n_sells = sum(1 for f in pf.fills if f.side == "sell")

    def flag(ok: bool) -> str:
        return "✅" if ok else "⚠️ "

    print("\n" + "=" * 60)
    print(" SNEAKER MARKET MAKER — paper run summary")
    print("=" * 60)
    print(f" ticks run          : {len(history)}  ({len(history) * tick_days:.1f} sim-days)")
    print(f" SKUs quoted        : {len(cfg.universe)}")
    print(f" fills              : {n_buys} buys / {n_sells} sells "
          f"({len(pf.round_trips)} round-trips, {n_hedges} hedge liquidations)")
    print(f" units still held   : {pf.total_units_held}")
    print(f" realised P&L       : ${pf.realised_pnl:,.2f}")
    print(f" ending equity      : ${final.equity:,.2f}  (peak ${final.peak_equity:,.2f})")
    print(f" kill-switch        : {'TRIPPED' if final.halted else 'clear'}")
    print("-" * 60)
    print(" KPIs (PRD §7)")
    print(f"  {flag(avg_margin >= 0.08)} net spread / round-trip : "
          f"{avg_margin:6.2%}   (target ≥ 8%)")
    print(f"  {flag(0 < avg_hold < 11)} avg holding time        : "
          f"{avg_hold:6.2f}d  (target < 11d)")
    print(f"  {flag(max_dd < 0.05)} max drawdown            : "
          f"{max_dd:6.2%}   (target < 5%)")
    print("=" * 60 + "\n")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="smm", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run a paper-trade simulation")
    r.add_argument("--config", default="config/default.toml")
    r.add_argument("--steps", type=int, default=None)
    r.add_argument("--seed", type=int, default=None)
    r.set_defaults(func=_cmd_run)

    q = sub.add_parser("quote", help="inspect the A–S pricing core vs. inventory")
    q.add_argument("--config", default="config/default.toml")
    q.add_argument("--sku", default=None, help='SKU key "Model|Size"')
    q.set_defaults(func=_cmd_quote)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

# Sneaker Market Maker

An algorithmic market maker for the sneaker resale secondary market.
**Phase 2** ([`smm/`](smm/)) is a config-driven, headless system (PRD §6) that
runs the full **quote → fill → hedge** loop end-to-end in **paper-trade mode**,
with a *real* Avellaneda–Stoikov pricing core and *mocked* market data and
execution. **Phase 3** ([`phase3/`](phase3/)) adds a replay backtester,
fill-intensity calibration, and a cross-platform arbitrage signal on top —
without changing any Phase-2 code.

📄 **Docs:** [Product Requirements (PRD)](docs/PRD.md) ·
[Technical Design Document](docs/TECHNICAL_DESIGN.md)

Zero third-party dependencies — pure Python 3.11 stdlib (`tomllib`, `dataclasses`,
`statistics`, `random`). It runs the moment you clone it.

## How to run

**Requirements:** Python **3.11+** (needs the stdlib `tomllib`). No `pip install`,
no virtualenv, no network. Run every command from the repo root.

```bash
git clone https://github.com/sinharahul/claude-sneaker-marketmaker.git
cd claude-sneaker-marketmaker
python3 --version          # expect 3.11 or newer
```

### Phase 2 — paper simulator (`smm/`)

```bash
# 1. inspect the pricing core: how quotes skew as inventory changes
python3 -m smm.cli quote --config config/default.toml

# 2. run a paper simulation and print the PRD §7 KPIs
python3 -m smm.cli run --config config/default.toml --steps 500 --seed 7
```

`quote` prints a table (flat → symmetric spread; at the cap → δ_ask goes negative
to liquidate). `run` prints a summary ending in the three KPIs:

```
  ✅ net spread / round-trip : 12.18%   (target ≥ 8%)
  ✅ avg holding time        :   1.26d  (target < 11d)
  ✅ max drawdown            :  0.17%   (target < 5%)
```

Tune everything (universe, fees, γ/κ, risk caps) in
[config/default.toml](config/default.toml) — the engine takes no hardcoded params.

### Phase 3 — backtester, calibration, arbitrage (`phase3/`)

Additive package that imports `smm` but never modifies it. Typical flow is
**record → backtest**, with `arb-demo` and `calibrate` as standalone tools:

```bash
# 1. record a replayable market-history CSV (written to data/, which is gitignored)
python3 -m phase3.cli record --out data/history.csv --steps 800 --dislocation-prob 0.03

# 2. replay that history through the engine + cross-platform arbitrage pass
python3 -m phase3.cli backtest --history data/history.csv

# 3. list the profitable cross-platform arbs found in the history
python3 -m phase3.cli arb-demo --history data/history.csv

# 4. calibrate fill-intensity A, κ — recovery test on synthetic fills...
python3 -m phase3.cli calibrate --demo
#    ...or fit from your own fills log (CSV with columns: delta,filled)
python3 -m phase3.cli calibrate --fills your_fills.csv --dt 0.0417
```

`--help` works on any command (e.g. `python3 -m phase3.cli backtest --help`).

### Tests

```bash
python3 -m unittest discover -s tests      # 17 tests (10 Phase-2 + 7 Phase-3), no pytest needed
```

## What's real vs. mocked

| Layer | Status | Where |
|---|---|---|
| **Quoting engine** (Avellaneda–Stoikov closed form) | **real** | [pricing.py](smm/engine/pricing.py) |
| Risk breakers & inventory caps | **real** | [risk.py](smm/engine/risk.py) |
| Hedging / liquidation policy (H_t) | **real** | [hedging.py](smm/engine/hedging.py) |
| Ingestion: VWAP mid + rolling vol | **real** | [pipeline.py](smm/data/pipeline.py) |
| Portfolio / P&L / KPI accounting | **real** | [portfolio.py](smm/portfolio.py) |
| Market-data feed | **mocked** (synthetic GBM) | [sources.py](smm/data/sources.py) |
| Order execution | **mocked** (paper fills) | [router.py](smm/execution/router.py) |

Live `StockXSource` / `EbaySource` / `LiveExecutor` are stubbed with the honest
constraints inline. **Live order placement is intentionally not implemented** —
executing real buys/sells against a funded account is an operator-run action.

## Architecture

```
                 ┌──────────────┐   snapshots   ┌───────────────┐  RefPrice(P_t, σ_t)
 MarketDataSource│  Module 1    │──────────────▶│  Pipeline     │────────────────┐
 (mock | stockx) │ Data Engine  │               │ VWAP mid,vol  │                │
                 └──────────────┘               └───────────────┘                ▼
                                                                        ┌──────────────────┐
   ┌────────────────────────────────────────────────────────────┐      │  Module 2        │
   │ Portfolio: cash, inventory I_t, realised P&L, round-trips   │◀─────│ RiskManager +    │
   └────────────────────────────────────────────────────────────┘      │ A–S QuoteEngine  │
                          ▲            ▲                                 └──────────────────┘
                          │ fills      │ hedge fill                               │ Quote(δbid,δask)
                          │            │                                          ▼
                    ┌───────────────┐  │                              ┌──────────────────┐
                    │ Module 3      │◀─┴──────────────────────────────│ HedgingPolicy H_t│
                    │ PaperExecutor │   λ(δ)=A·e^(−κδ) fill model      └──────────────────┘
                    └───────────────┘
```

The orchestrator [app.py](smm/app.py) drives one tick at a time.

## The pricing core (why not tabular Bellman?)

The PRD frames quoting as a Bellman value function `V(I, P)`. Solving that by
tabular DP across 5,000 SKUs × continuous `(P, σ, Δt)` is a curse-of-
dimensionality trap. **Avellaneda–Stoikov (2008)** gives the *closed form* for
exactly this reward — spread capture minus an inventory-variance penalty
`γ·I²·σ²`:

```
reservation r = P − q·γ·σ²·(T−t)
half-spread   = ½·γ·σ²·(T−t) + (1/γ)·ln(1 + γ/κ)     (× capacity-utilisation widening)
bid = r − half,   ask = r + half
```

Inventory skew falls out for free (`smm quote` output):

```
 inv       bid       ask    reserv    δbid    δask
  -5   $312.88   $352.47 $332.67     7.12   32.47   short → bid aggressive (buy back)
   0   $302.40   $337.60 $320.00    17.60   17.60   flat  → symmetric, max capture
  20   $242.92   $295.71 $269.31    77.08  -24.29   at cap → sell below mid to liquidate
```

The only genuine dynamic-programming decision left is the discrete hedge action
`H_t ∈ {−1,0,1}` in [hedging.py](smm/engine/hedging.py).

## Data access reality (read before wiring live feeds)

FR-1.1 asks for **L2 depth at 500 ms** across StockX/GOAT/eBay. That does not
exist through legitimate channels:

- **StockX** — approval-gated developer API; **top of book only** (lowest ask,
  highest bid, recent sales), not full depth. Poll cadence is seconds.
- **GOAT** — no public API; scraping violates ToS. Treat as read-only/manual.
- **eBay** — real Browse/Buy APIs, but fixed-price/auction, not a bid-ask book.

The state model reflects this: it carries per-platform **best bid/ask**, not a
synthetic order book. Treat FR-1.1 as "fastest the API allows."

## Economics baked into the config

All-in platform fees (~10.5% sell + 3% buy here) are why the ≥8% net-spread KPI
is hard: you need ~18–22% *gross* spread, which lives only on illiquid SKUs that
fill slowly — the central tension between the margin KPI and the <11-day
turnover KPI. Tune it all in [config/default.toml](config/default.toml).

## Phase 3 (implemented — `phase3/`)

Additive package: it imports `smm` and composes its components, but changes no
Phase-2 file, so `python3 -m smm.cli run` behaves exactly as before.

| Roadmap item | Status | Module |
|---|---|---|
| Calibrate `κ` / `A` from realised fill data | ✅ done | [calibrate.py](phase3/calibrate.py) |
| Backtester over recorded price history | ✅ done | [recorder.py](phase3/recorder.py) → [replay.py](phase3/replay.py) → [backtest.py](phase3/backtest.py) |
| Cross-platform arbitrage → `Hedge.ACCUMULATE` | ✅ done | [arbitrage.py](phase3/arbitrage.py) |
| Operator-gated live execution adapter | ◐ stub (dry-run by default, never autonomous) | [live_gateway.py](phase3/live_gateway.py) |
| Real `StockXSource` / `EbaySource` live feeds | ▢ next | interface in [sources.py](smm/data/sources.py) |

See [How to run → Phase 3](#phase-3--backtester-calibration-arbitrage-phase3) for
the commands.

## Roadmap (Phase 4)

1. Real `StockXSource` + `EbaySource` behind the existing `MarketDataSource` interface.
2. Model per-leg fill latency in the arb path (the backtester currently treats a
   paired cross-platform trade as instant/self-liquidating — an upper bound).
3. Wire an operator-authenticated client behind `LiveGateway`, keeping the
   per-order confirmation gate.

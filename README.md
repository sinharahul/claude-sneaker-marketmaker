# Sneaker Market Maker — Phase 2 (headless skeleton)

An algorithmic market maker for the sneaker resale secondary market. This is the
Phase-2 scaffold: a config-driven, headless system (PRD §6) that runs the full
**quote → fill → hedge** loop end-to-end in **paper-trade mode**, with a *real*
pricing core and *mocked* market data and execution.

Zero third-party dependencies — pure Python 3.11 stdlib (`tomllib`, `dataclasses`,
`statistics`, `random`). It runs the moment you clone it.

```bash
# inspect the pricing core (how quotes skew with inventory)
python3 -m smm.cli quote --config config/default.toml

# run a paper simulation and print the PRD §7 KPIs
python3 -m smm.cli run --config config/default.toml --steps 500 --seed 7

# tests (no pytest needed)
python3 -m unittest discover -s tests
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

## Roadmap (Phase 3)

1. Real `StockXSource` + `EbaySource` behind the existing `MarketDataSource` interface.
2. Calibrate `κ` / `A` from realised fill data instead of assuming them.
3. Backtester over recorded price history (replace `MockSource` with a replay source).
4. Cross-platform arbitrage signal to emit `Hedge.ACCUMULATE`.
5. Operator-gated live execution adapter (never fully autonomous).

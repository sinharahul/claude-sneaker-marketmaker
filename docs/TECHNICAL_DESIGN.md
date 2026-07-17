# Technical Design Document

> Sneaker Secondary-Market Algorithmic Market Maker — implementation of
> [PRD.md](PRD.md)

- **Status:** Phase 2 (paper simulator) + Phase 3 (backtester) implemented.
- **Language/runtime:** Python 3.11+, **zero third-party dependencies** (stdlib only).
- **Repo layout:** `smm/` (Phase 2), `phase3/` (Phase 3, additive), `tests/`, `config/`.

---

## 1. Design principles

1. **Real core, mocked edges.** The pricing/risk/hedging math is production-grade;
   the market feed and order execution are mocked so the whole system runs
   offline and deterministically. Live I/O is where correctness is *cheapest* to
   add later and *most dangerous* to fake now.
2. **Headless & config-driven** (PRD §6). No UI. Every economic assumption lives
   in [`config/default.toml`](../config/default.toml); the engine takes no
   hardcoded parameters, which makes runs reproducible and auditable.
3. **Zero dependencies.** Pure stdlib (`tomllib`, `dataclasses`, `statistics`,
   `random`, `csv`). Clone-and-run, no environment setup, trivial to review.
4. **Determinism.** All randomness flows through seeded `random.Random` streams,
   so a given `(config, seed)` reproduces an identical equity path — a hard
   requirement for backtesting and regression tests.
5. **Phase isolation.** `phase3/` imports from `smm/` but never modifies it, so
   `python3 -m smm.cli run` is byte-for-byte unaffected by Phase-3 work.

---

## 2. System architecture

```
                 ┌──────────────┐   snapshots   ┌───────────────┐  RefPrice(P_t, σ_t)
 MarketDataSource│  Module 1    │──────────────▶│  Pipeline     │────────────────┐
 (mock | replay) │ Data Engine  │               │ VWAP mid, vol │                │
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

The orchestrator [`smm/app.py`](../smm/app.py) drives one tick at a time; the
Phase-3 [`phase3/backtest.py`](../phase3/backtest.py) runs an equivalent loop with
an added arbitrage pass.

### Per-tick control flow (per SKU)
1. **Data Engine** ingests a `MarketSnapshot` → derives `RefPrice(mid, σ)`.
2. **RiskManager** evaluates breakers/caps → decides which quote sides to suppress.
3. **Quoting Engine** produces an Avellaneda–Stoikov `Quote` given inventory.
4. **Executor** simulates fills against the fill-intensity model.
5. **HedgingPolicy** decides $H_t$; executor performs any liquidation.
6. **Portfolio** books fills, marks equity, updates the drawdown kill-switch.

---

## 3. Module design & FR traceability

| PRD requirement | Where implemented | Notes |
|---|---|---|
| FR-1.1 ingest order book | [`data/sources.py`](../smm/data/sources.py) | **top-of-book**, not L2 — see §7 |
| FR-1.2 VWAP synthetic mid | [`data/pipeline.py`](../smm/data/pipeline.py) `_vwap_mid` | weighted by book tightness (1/spread) |
| FR-1.3 rolling vol 1h/24h/7d | [`data/pipeline.py`](../smm/data/pipeline.py) `_rolling_vol` | std of log-returns, scaled to per-day |
| FR-2.1 widen on vol/capacity | [`engine/pricing.py`](../smm/engine/pricing.py) | utilisation multiplier on half-spread |
| FR-2.2 asymmetric inventory skew | [`engine/pricing.py`](../smm/engine/pricing.py) | falls out of the A–S reservation price |
| FR-3.1 cross-platform arb pathways | [`phase3/arbitrage.py`](../phase3/arbitrage.py) | ACCUMULATE signal ($H_t=+1$) |
| FR-3.2 instant-payout liquidation | [`engine/hedging.py`](../smm/engine/hedging.py) | risk-adjusted hold vs. cash-out |
| §5 vol circuit breaker | [`engine/risk.py`](../smm/engine/risk.py) | short-σ ≥ 3× baseline halts bids |
| §5 inventory caps | [`engine/risk.py`](../smm/engine/risk.py) | hard per-SKU cap suppresses bids |
| §5 drawdown limit | [`engine/risk.py`](../smm/engine/risk.py) | latching kill-switch vs. bankroll |
| §7 KPIs | [`smm/cli.py`](../smm/cli.py) `_print_summary` | net spread, holding time, drawdown |

---

## 4. Mathematical framework — the key design decision

### 4.1 Why not tabular Bellman
The PRD frames quoting as a Bellman value function $V(I,P)$ solved by dynamic
programming. Solving that *tabularly* across 5,000 SKUs × a continuous
$(P, \sigma, \Delta t)$ state grid is a curse-of-dimensionality trap: the table
size explodes and every quote update becomes a grid sweep, blowing the FR-1.1
latency budget and the §5 scalability target.

### 4.2 Avellaneda–Stoikov closed form
Avellaneda & Stoikov (2008) derive the *closed-form* optimum for **exactly the
PRD reward** — spread capture minus the inventory-variance penalty
$\gamma_{risk} I^2 \sigma^2$. Each SKU's quotes become O(1) arithmetic:

$$r = P - q\,\gamma\,\sigma^2 (T-t) \qquad \text{(reservation price)}$$

$$\text{half-spread} = \tfrac{1}{2}\gamma\,\sigma^2(T-t) + \tfrac{1}{\gamma}\ln\!\left(1 + \tfrac{\gamma}{\kappa}\right)$$

$$P^{bid} = r - \text{half}, \qquad P^{ask} = r + \text{half}$$

- $\sigma$ is a **dollar** volatility (fractional σ × mid), so $\gamma, \kappa$
  carry units $1/\$$.
- A **capacity-utilisation multiplier** widens the half-spread as
  $|I|/I_{max} \to 1$ (FR-2.1), and a `min_half_pct` floor prevents quoting
  inside a minimum spread.

**Inventory skew is automatic (FR-2.2):** because the skew shifts *both* quotes by
the same $q\gamma\sigma^2(T-t)$, the total spread ($2\times$half) is inventory-
independent, but the *asymmetry* clears inventory. Sample from `smm quote`:

```
 inv       bid       ask    reserv    δbid    δask
  -5   $312.88   $352.47 $332.67     7.12   32.47   short → bid aggressive (buy back)
   0   $302.40   $337.60 $320.00    17.60   17.60   flat  → symmetric, max capture
  20   $242.92   $295.71 $269.31    77.08  -24.29   at cap → δask < 0, sell below mid to liquidate
```

The only genuine DP decision left is the discrete hedge action
$H_t \in \{-1,0,1\}$ (§5 below).

### 4.3 Fill-intensity model
Execution probability follows $\lambda(\delta) = A\,e^{-\kappa\delta}$; over a tick
of length $dt$, a resting quote fills with probability $1 - e^{-\lambda(\delta)dt}$.
Tighter quotes fill more often — this couples the spread choice to realised
turnover, so paper KPIs are honest rather than assumed. In Phase 3, $A$ and
$\kappa$ are **calibrated from data** rather than guessed (§6.2).

### 4.4 Hedging policy (risk-adjusted, FR-3.2)
A naïve "liquidate if instant-payout > depreciated hold value" never fires,
because the instant cash-out haircut always loses to holding. The policy instead
compares the payout against a **risk-adjusted** hold value that subtracts the
same $\gamma\sigma^2$ inventory penalty the pricing core uses:

$$\text{hold} = P(1 - \text{decay}\cdot\text{horizon}) - \gamma\,\sigma_\$^2\,\text{horizon}\cdot q$$

$$\text{liquidate if } \text{over soft cap} \ \wedge\ \text{payout}_{now} \ge \text{hold}$$

This makes fire-sales fire when **inventory risk** (not merely depreciation) is
the binding cost — consistent with the MDP objective.

---

## 5. Risk controls (PRD §5)

- **Inventory cap** — a hard per-SKU limit suppresses the bid at capacity.
- **Volatility circuit breaker** — when short-window σ ≥ `vol_breaker_sigma` ×
  long-window baseline, bids are halted (don't accumulate into a restock crash).
- **Drawdown kill-switch** — latches globally if equity draws down past
  `max_drawdown_halt_pct` **of the deployed bankroll**. Measuring against a fixed
  bankroll (not peak-from-zero) avoids a degenerate early trip; see §7.

All three are deterministic and side-effect-free, so every suppressed quote has an
auditable reason string.

---

## 6. Phase 3 — additive extensions (`phase3/`)

Imports `smm`, composes its components, modifies nothing.

### 6.1 Replay backtester
`recorder.py` captures a fixed price path to CSV (long format:
`tick,model,size,platform,bid,ask,last_sale`), optionally injecting cross-platform
**dislocations** so the arb detector has something to find. `replay.py` exposes
that CSV as a drop-in `MarketDataSource`. `backtest.py` runs the Phase-2 engine
over it with an added arbitrage pass, booking arb P&L to a **separate ledger** so
it doesn't distort the market-making holding-time KPI.

### 6.2 Fill-intensity calibration
`calibrate.py` recovers $(A, \kappa)$ from a fills log by binning δ, converting
empirical fill rates to intensities $\lambda = -\ln(1-p)/dt$, and running a
count-weighted OLS of $\ln\lambda$ on δ (slope $\to -\kappa$, intercept
$\to \ln A$). The `--demo` recovery test recovers known parameters to **<1% error,
R² = 0.999**.

### 6.3 Cross-platform arbitrage → ACCUMULATE
`arbitrage.py` finds, per SKU, the best cross-platform pathway (highest bid on one
venue vs. lowest ask on another) and reports **gross and net** edge after the full
fee load. Given ~13.5% round-trip fees, only sizeable dislocations clear the
hurdle — which is precisely why the PRD's 10–30% spreads persist instead of being
arbed away.

### 6.4 Operator-gated live execution
`live_gateway.py` is a deliberate **stub**: dry-run by default, and even with
`armed=True, confirm=True` it raises `NotImplementedError`. Placing real orders
against a funded account is an operator-run action, never autonomous. It exists to
define the safe interface Phase 4 must implement behind.

---

## 7. Deliberate deviations from the PRD

These are places where the spec's assumptions do not survive contact with reality;
each is a conscious, documented choice.

| PRD assumption | Reality & what we did |
|---|---|
| **FR-1.1: L2 depth @ 500 ms** across StockX/GOAT/eBay | No legitimate L2 feed exists. StockX API is approval-gated, **top-of-book only**; GOAT has no public API (scraping violates ToS); eBay is fixed-price/auction. The state carries **per-platform best bid/ask**, and FR-1.1 is treated as "fastest the API allows" (seconds, not ms). |
| **Tabular Bellman** over full state | Replaced with the **closed-form A–S** optimum for the same reward (§4). |
| **KPI: ≥8% net spread** | Achievable only on illiquid SKUs, which fill slowly — directly in tension with the <11-day turnover KPI. Fees (~10.5% sell + 3% buy) mean ~18–22% *gross* is needed. Surfaced, not hidden. |
| **Drawdown measured "in an event"** | Naïve peak-from-zero drawdown trips the kill-switch after the first fill. Fixed by measuring against a configured **bankroll** (`starting_cash`). |
| **FR-3.2 instant-payout rule** | The stated rule never fires; replaced with a **risk-adjusted** hold value (§4.4). |
| **Arb is instantaneous** | The backtester models a paired cross-platform trade as instant/self-liquidating — an **upper bound**. Real arbs carry settlement lag and per-leg listing risk (Phase-4 item). |

---

## 8. Configuration

Single source of truth: [`config/default.toml`](../config/default.toml).
Sections: `[run]` (steps, tick length, seed, bankroll), `[pricing]`
(γ, κ, horizon, floors), `[fills]` (A), `[fees]`, `[risk]` (caps, breakers,
drawdown), `[hedging]` (decay, soft ratio), and a `[[universe]]` array of quotable
SKUs. Loaded into an immutable typed `Config` ([`smm/config.py`](../smm/config.py))
so the engine is parameter-free.

---

## 9. Testing

`python3 -m unittest discover -s tests` — **17 tests**, no pytest:

- **Pricing** (`tests/test_pricing.py`): symmetric-when-flat, skew direction for
  long/short, spread widening on vol and capacity, min-spread floor, hedging
  liquidation at cap, deterministic reproducibility.
- **Phase 3** (`tests/test_phase3.py`): record→replay round-trip, calibration
  parameter recovery, arb detection (planted + negative), backtester runs +
  determinism.

---

## 10. Roadmap (Phase 4)

1. Real `StockXSource` / `EbaySource` behind the existing `MarketDataSource`.
2. Model per-leg fill latency in the arb path (remove the instant-arb upper bound).
3. Wire an operator-authenticated client behind `LiveGateway`, keeping the
   per-order confirmation gate.
4. Calibrate γ/decay from realised P&L attribution, closing the loop between the
   risk model and observed inventory outcomes.

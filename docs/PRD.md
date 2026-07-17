# Product Requirements Document (PRD)

> Sneaker Secondary-Market Algorithmic Market Maker (AMM)

## 1. Executive Summary & Problem Statement

### The Opportunity
The sneaker reselling secondary market experiences significant liquidity
fragmentation across platforms like StockX, GOAT, and eBay. This fragmentation
creates structural inefficiencies, resulting in wide bid-ask spreads (10% to 30%)
on high-demand, low-velocity releases.

### The Solution
This project introduces an automated Algorithmic Market Maker (AMM) tailored for
the sneaker resale ecosystem. By utilizing Bellman Hedging (Dynamic Programming),
the algorithm optimizes inventory positioning, dynamic bid-ask quoting, and
risk-mitigating hedge execution. The system captures the spread while dynamically
managing the high volatility and depreciation risk inherent in sneaker assets.

## 2. Assumptions & System Constraints

- **Platform Access:** Connection is established via private or public APIs on
  primary secondary markets (e.g., StockX, GOAT).
- **Inventory Risk:** Physical inventory storage is required, requiring a finite
  capacity constraint in the mathematical model.
- **Settlement Time:** Physical authentication introduces a 3 to 7-day
  cash-to-cash latency, which must be factored into the discount factor (γ).

## 3. Core Architecture & Mathematical Framework

The core engine models the market-making operation as a Markov Decision Process
(MDP) solved via Bellman Optimality equations to maximize terminal wealth while
minimizing inventory risk.

### State Space (S)
At any time $t$, the state is defined by the tuple:

$$S_t = (I_t,\ P_t,\ \sigma_t,\ \Delta t_{release})$$

- $I_t \in \mathbb{Z}$: Current physical inventory count of a specific sneaker SKU/Size.
- $P_t \in \mathbb{R}^+$: Mid-market reference price.
- $\sigma_t$: Implied localized market volatility.
- $\Delta t_{release}$: Time elapsed since the initial retail drop date (controls asset decay).

### Action Space (A)
The algorithm outputs a quote control vector at each interval:

$$A_t = (\delta_t^{bid},\ \delta_t^{ask},\ H_t)$$

- $\delta_t^{bid}$: Distance of the buy quote below the mid-market price ($P^{bid} = P_t - \delta_t^{bid}$).
- $\delta_t^{ask}$: Distance of the sell quote above the mid-market price ($P^{ask} = P_t + \delta_t^{ask}$).
- $H_t \in \{-1, 0, 1\}$: Hedging action execution (e.g., cross-platform shorting,
  quick-liquidation fire sales, or bulk consignment).

### The Bellman Optimality Equation
The value function $V(I, P)$ balances short-term trading profits against
inventory holding costs:

$$V(I_t, P_t) = \max_{\delta^{bid}, \delta^{ask}, H} \left\{ \mathbb{E} \left[ R_t(I_t, \delta^{bid}, \delta^{ask}, H) + \gamma V(I_{t+1}, P_{t+1}) \mid S_t \right] \right\}$$

Where the immediate reward function $R_t$ penalizes large inventory deviations
using a risk-aversion coefficient ($\gamma_{risk}$):

$$R_t = \lambda_{bid}(\delta^{bid})\delta^{bid} + \lambda_{ask}(\delta^{ask})\delta^{ask} - \gamma_{risk} \cdot (I_t)^2 \cdot \sigma_t^2 - \text{Cost}(H_t)$$

- $\lambda_{bid}, \lambda_{ask}$: Probability distribution functions of order execution based on quote distance.
- $\gamma$: Temporal discount factor factoring in platform capital lockup.

## 4. Functional Requirements

### Module 1: Market Data Ingestion Pipeline (Data Engine)
- **FR-1.1:** The system must ingest order book depth (L2 data) from StockX, GOAT,
  and eBay at a maximum latency of 500ms.
- **FR-1.2:** Calculate a real-time synthetic mid-market reference price ($P_t$)
  using volume-weighted average price (VWAP) across all platforms.
- **FR-1.3:** Compute localized rolling volatility ($\sigma_t$) across 1-hour,
  24-hour, and 7-day windows.

### Module 2: Bellman Quoting Optimization Engine (Core Algo)
- **FR-2.1:** Dynamically widen spreads ($\delta_t^{bid} + \delta_t^{ask}$) when
  inventory ($I_t$) approaches max capacity or when volatility ($\sigma_t$) spikes.
- **FR-2.2:** Asymmetrically shift quotes to clear inventory: if inventory is high
  ($I_t \gg 0$), lower $\delta_t^{ask}$ to trigger faster sales and increase
  $\delta_t^{bid}$ to discourage buys.

```
[Low Inventory: I_t < 0]  --> Shift Quotes Downward --> Lower Bid (Buy Cheap) / Lower Ask (Sell Faster)
[Target Inventory: I_t=0] --> Symmetric Spread      --> Maximize Spread Capture
[High Inventory: I_t > 0] --> Shift Quotes Upward   --> Higher Bid (Block Buys) / Lower Ask (Liquidate)
```

### Module 3: Cross-Platform Hedging Execution (Order Routing)
- **FR-3.1:** Monitor alternative platforms for arbitrage pathways when local
  Bellman hedging constraints require inventory reduction ($H_t \neq 0$).
- **FR-3.2:** Automatically execute instant-payout sales (e.g., GOAT instant
  cash-out) if the physical depreciation rate exceeds the algorithm's expected
  value threshold.

## 5. Non-Functional & Operational Requirements

- **Scalability:** The algorithmic engine must evaluate and update quotes for up
  to 5,000 unique SKU/Size combinations simultaneously.
- **Risk Circuit Breakers:** Automatically halt bidding if market volatility
  exceeds 3σ within a rolling 15-minute window (e.g., during sudden retail restocks).
- **Inventory Caps:** Hardcode strict structural limits per SKU (e.g., max 20 pairs
  per size) to prevent over-exposure to a single sneaker model trend.

## 6. Scope & Non-Goals

### Out of Scope for Phase 1
- **Manual Trading UI:** The initial architecture operates as a headless, purely
  programmatic system via CLI config.
- **Retail Botting Integration:** The algorithm will not buy directly from retail
  releases (SNKRS, Shopify drops). It operates strictly within secondary market
  maker spreads.
- **Counterfeit Detection:** Assumes all inventory handled via platforms is
  verified by their respective internal authentication centers.

## 7. Success Metrics (KPIs)

- **Net Profit Margin:** Capture an average net spread of ≥ 8% per round-trip
  trade after platform transaction fees.
- **Inventory Turnover Ratio (ITR):** Maintain an average holding time of less
  than 11 days per asset unit.
- **Maximum Drawdown:** Limit algorithmic capital drawdown to < 5% in any single
  high-volatility restock event.

---

*See [TECHNICAL_DESIGN.md](TECHNICAL_DESIGN.md) for how these requirements are
realized in code, including the deliberate deviations where the PRD's assumptions
do not survive contact with the real platforms.*

"""Operator-gated live execution gateway (roadmap items 1 & 5).

This is deliberately NOT a working live trader. Placing real buy/sell orders
against a funded StockX/GOAT/eBay account is a side-effectful financial action
that must be run by the operator under their own credentials with an explicit,
per-order trigger — never autonomously by the algorithm.

The gateway therefore:
  * defaults to `armed=False` (dry-run) — every order is logged, none is sent;
  * requires BOTH `armed=True` AND an explicit per-call `confirm=True` before it
    would ever route, and even then raises `NotImplementedError` because no real
    client is wired in;
  * exists to define the safe interface Phase-4 must implement behind.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OrderIntent:
    key: str
    side: str          # "buy" | "sell"
    platform: str
    limit_price: float
    reason: str = ""


@dataclass
class LiveGateway:
    armed: bool = False
    dry_run_log: list[OrderIntent] = field(default_factory=list)

    def submit(self, intent: OrderIntent, *, confirm: bool = False) -> str:
        """Stage an order. In dry-run (default) it is only logged and returned."""
        self.dry_run_log.append(intent)
        if not self.armed or not confirm:
            return (f"DRY-RUN [{intent.side} {intent.key} @ {intent.limit_price:.2f} "
                    f"on {intent.platform}] — not sent (armed={self.armed}, confirm={confirm})")
        raise NotImplementedError(
            "Live routing is intentionally unimplemented. Wire a per-platform, "
            "operator-authenticated client here and keep the per-order confirm gate."
        )

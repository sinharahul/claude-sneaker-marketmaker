"""Record a market-history CSV that the backtester can replay deterministically.

Decoupling data generation from the sim is the whole point of a backtester: you
capture a fixed price path once, then replay it against many parameter sets and
get apples-to-apples comparisons. Here we source the path from the Phase-2
`MockSource` (synthetic GBM), optionally injecting cross-platform *dislocations*
so the Phase-3 arbitrage detector has something real to find (the tight
synthetic feed never crosses on its own).

CSV schema (long format, one row per tick × SKU × platform):
    tick,model,size,platform,bid,ask,last_sale
"""

from __future__ import annotations

import csv
import random
from pathlib import Path

from smm.config import Config
from smm.data.sources import MockSource


def record(
    cfg: Config,
    out_path: str | Path,
    *,
    steps: int,
    seed: int | None = None,
    dislocation_prob: float = 0.0,
    dislocation_size: float = 0.22,
) -> Path:
    """Generate `steps` ticks of market history and write them to `out_path`.

    `dislocation_prob` is the per-(SKU,tick) chance that one random platform's
    book is shifted by ±`dislocation_size`, mimicking a stale/mispriced venue
    during a hype spike — the condition that creates a crossable arb.
    """
    seed = cfg.run.seed if seed is None else seed
    src = MockSource(cfg, random.Random(seed))
    diz = random.Random(seed + 999)  # independent stream for dislocations

    # Map key -> (model, size) so we can write human-readable rows.
    label = {s.key: (s.model, s.size) for s in cfg.universe}

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["tick", "model", "size", "platform", "bid", "ask", "last_sale"])
        for t in range(steps):
            snaps = src.tick(t)
            for key, snap in snaps.items():
                books = dict(snap.books)
                if dislocation_prob and diz.random() < dislocation_prob:
                    _dislocate(books, diz, dislocation_size)
                model, size = label[key]
                for platform, (bid, ask) in books.items():
                    w.writerow([t, model, size, platform,
                                _fmt(bid), _fmt(ask), _fmt(snap.last_sale)])
    return out_path


def _dislocate(books: dict, rng: random.Random, size: float) -> None:
    """Shift one platform's whole book up or down by `size` (in place)."""
    platform = rng.choice(list(books))
    factor = 1.0 + rng.choice((-1.0, 1.0)) * size
    bid, ask = books[platform]
    books[platform] = (
        round(bid * factor, 2) if bid is not None else None,
        round(ask * factor, 2) if ask is not None else None,
    )


def _fmt(x: float | None) -> str:
    return "" if x is None else f"{x:.2f}"

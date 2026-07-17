"""ReplaySource — a MarketDataSource backed by a recorded history CSV.

Drop-in for `MockSource`: it satisfies the same `smm.data.sources.MarketDataSource`
interface, so the exact Phase-2 engine/executor stack runs against recorded
prices with zero changes to `smm`.
"""

from __future__ import annotations

import csv
from pathlib import Path

from smm.data.sources import MarketDataSource
from smm.types import MarketSnapshot, sku_key


class ReplaySource(MarketDataSource):
    def __init__(self, path: str | Path) -> None:
        # tick -> key -> MarketSnapshot
        self._by_tick: dict[int, dict[str, MarketSnapshot]] = {}
        self._keys: set[str] = set()
        self._load(Path(path))
        self.num_ticks = (max(self._by_tick) + 1) if self._by_tick else 0

    def _load(self, path: Path) -> None:
        with path.open(newline="") as fh:
            for row in csv.DictReader(fh):
                t = int(row["tick"])
                key = sku_key(row["model"], row["size"])
                self._keys.add(key)
                snap = self._by_tick.setdefault(t, {}).get(key)
                if snap is None:
                    snap = MarketSnapshot(key=key, tick=t, books={},
                                          last_sale=_num(row["last_sale"]))
                    self._by_tick[t][key] = snap
                snap.books[row["platform"]] = (_num(row["bid"]), _num(row["ask"]))

    @property
    def keys(self) -> set[str]:
        return set(self._keys)

    def tick(self, t: int) -> dict[str, MarketSnapshot]:
        try:
            return self._by_tick[t]
        except KeyError:
            raise IndexError(
                f"replay history has no tick {t} (available 0..{self.num_ticks - 1})"
            ) from None


def _num(s: str | None) -> float | None:
    return float(s) if s not in (None, "") else None

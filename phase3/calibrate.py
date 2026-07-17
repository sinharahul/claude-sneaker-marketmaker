"""Calibrate the fill-intensity parameters A and κ from realised fill data.

The Phase-2 executor *assumes* λ(δ) = A·e^(−κδ). In production those numbers must
come from data, not a config guess. Given a log of quoted half-distances δ, the
tick length dt, and whether each quote filled, we recover (A, κ):

    p(fill | δ) = 1 − exp(−λ(δ)·dt)   ⇒   λ(δ) = −ln(1 − p)/dt
    ln λ(δ) = ln A − κ·δ              ⇒   weighted OLS of ln λ on δ

Slope → −κ, intercept → ln A. Binning δ and weighting each bin by its sample
count keeps the regression stable when fills are sparse. `demo_recovery` proves
the estimator by generating fills from known parameters and recovering them.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass


@dataclass
class Bin:
    delta_mid: float
    n: int
    fills: int
    p_hat: float
    lam_hat: float


@dataclass
class CalibrationResult:
    A: float
    kappa: float
    r2: float
    n_obs: int
    bins: list[Bin]

    def summary(self) -> str:
        return (f"A={self.A:.3f}  κ={self.kappa:.4f}  "
                f"R²={self.r2:.3f}  (n={self.n_obs}, {len(self.bins)} usable bins)")


def fit_intensity(
    deltas: list[float],
    filled: list[bool],
    dt: float,
    *,
    n_bins: int = 20,
    min_bin: int = 20,
) -> CalibrationResult:
    """Recover (A, κ) from parallel lists of δ and fill outcomes."""
    if len(deltas) != len(filled):
        raise ValueError("deltas and filled must be the same length")
    if not deltas:
        raise ValueError("no observations to calibrate on")

    lo, hi = min(deltas), max(deltas)
    width = (hi - lo) / n_bins if hi > lo else 1.0
    counts = [0] * n_bins
    hits = [0] * n_bins
    for d, f in zip(deltas, filled):
        idx = min(n_bins - 1, int((d - lo) / width)) if width > 0 else 0
        counts[idx] += 1
        hits[idx] += 1 if f else 0

    bins: list[Bin] = []
    for i in range(n_bins):
        if counts[i] < min_bin:
            continue
        p = hits[i] / counts[i]
        if not (0.0 < p < 1.0):   # ln(1-p) undefined at the extremes
            continue
        lam = -math.log(1.0 - p) / dt
        bins.append(Bin(lo + (i + 0.5) * width, counts[i], hits[i], p, lam))

    if len(bins) < 2:
        raise ValueError("not enough usable bins to fit — need more spread in δ")

    # Weighted OLS of y = ln λ on x = δ, weights = bin counts.
    xs = [b.delta_mid for b in bins]
    ys = [math.log(b.lam_hat) for b in bins]
    ws = [float(b.n) for b in bins]
    slope, intercept, r2 = _weighted_ols(xs, ys, ws)

    return CalibrationResult(
        A=math.exp(intercept),
        kappa=-slope,
        r2=r2,
        n_obs=len(deltas),
        bins=bins,
    )


def _weighted_ols(xs, ys, ws):
    sw = sum(ws)
    swx = sum(w * x for w, x in zip(ws, xs))
    swy = sum(w * y for w, y in zip(ws, ys))
    swxx = sum(w * x * x for w, x in zip(ws, xs))
    swxy = sum(w * x * y for w, x, y in zip(ws, xs, ys))
    denom = sw * swxx - swx * swx
    if denom == 0:
        raise ValueError("degenerate regression (all δ identical)")
    slope = (sw * swxy - swx * swy) / denom
    intercept = (swy - slope * swx) / sw
    # weighted R²
    ybar = swy / sw
    ss_tot = sum(w * (y - ybar) ** 2 for w, y in zip(ws, ys))
    ss_res = sum(w * (y - (intercept + slope * x)) ** 2 for w, x, y in zip(ws, xs, ys))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return slope, intercept, r2


def demo_recovery(
    true_A: float = 4.0,
    true_kappa: float = 0.06,
    dt: float = 1.0,
    n: int = 100_000,
    delta_max: float = 45.0,
    seed: int = 7,
) -> tuple[CalibrationResult, float, float]:
    """Generate n fills from known (A, κ), then recover them. Returns
    (result, true_A, true_kappa) so callers can report the recovery error."""
    rng = random.Random(seed)
    deltas: list[float] = []
    filled: list[bool] = []
    for _ in range(n):
        d = rng.uniform(0.0, delta_max)
        lam = true_A * math.exp(-true_kappa * d)
        p = 1.0 - math.exp(-lam * dt)
        deltas.append(d)
        filled.append(rng.random() < p)
    return fit_intensity(deltas, filled, dt), true_A, true_kappa

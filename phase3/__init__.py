"""Phase 3 — additive extensions to the Phase-2 market maker.

This package ONLY imports from `smm`; it never modifies Phase-2 code, so the
Phase-2 paper simulator (`python3 -m smm.cli run`) keeps working unchanged.

Deliverables (from the README roadmap):
  1. Live feed adapters ............ live_gateway.py (operator-gated stub)
  2. Calibrate κ / A from fills .... calibrate.py
  3. Replay backtester ............. recorder.py + replay.py + backtest.py
  4. Cross-platform arb → ACCUMULATE  arbitrage.py

Everything runnable offline via:  python3 -m phase3.cli --help
"""

__version__ = "0.3.0"

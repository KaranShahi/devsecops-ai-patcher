"""Synthetic gold-like M5 data (regime-switching random walk). Only for tests / smoke runs;
it says NOTHING about whether the strategy works on real markets."""
from __future__ import annotations

import numpy as np
import pandas as pd


def make_m5(days: int = 120, seed: int = 1, start: str = "2026-01-05", price: float = 4000.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=days * 288, freq="5min")
    idx = idx[idx.dayofweek < 5]                       # no weekends
    n = len(idx)
    drift = np.zeros(n)
    regime_len = 288 * 5
    for s in range(0, n, regime_len):
        drift[s:s + regime_len] = rng.choice([-0.012, 0.0, 0.012])
    vol = 0.55 + 0.25 * np.sin(np.arange(n) / 288 * 2 * np.pi)   # intraday vol wave
    ret = drift + rng.normal(0, 1, n) * vol
    close = price + np.cumsum(ret)
    open_ = np.concatenate([[price], close[:-1]])
    wick = np.abs(rng.normal(0, 0.35, (n, 2)))
    high = np.maximum(open_, close) + wick[:, 0]
    low = np.minimum(open_, close) - wick[:, 1]
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close},
                        index=pd.DatetimeIndex(idx, name="time"))

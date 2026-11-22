"""Swing / market-structure primitives. Pure numpy, no look-ahead:
a pivot at index i is only reported once n further bars exist after it."""
from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view


def pivot_highs(high, n: int) -> np.ndarray:
    """Indices i where high[i] > the n bars before it and >= the n bars after it."""
    h = np.asarray(high, dtype=float)
    if n < 1 or len(h) < 2 * n + 1:
        return np.array([], dtype=int)
    win = sliding_window_view(h, 2 * n + 1)
    centre = win[:, n]
    mask = (centre > win[:, :n].max(axis=1)) & (centre >= win[:, n + 1:].max(axis=1))
    return np.nonzero(mask)[0] + n


def pivot_lows(low, n: int) -> np.ndarray:
    return pivot_highs(-np.asarray(low, dtype=float), n)


def true_range(h, l, c) -> np.ndarray:
    h, l, c = (np.asarray(x, dtype=float) for x in (h, l, c))
    tr = h - l
    if len(h) > 1:
        pc = c[:-1]
        tr[1:] = np.maximum(tr[1:], np.maximum(np.abs(h[1:] - pc), np.abs(l[1:] - pc)))
    return tr


def atr_at(tr: np.ndarray, i: int, period: int) -> float:
    """Simple-average ATR over the `period` bars ending at index i (inclusive)."""
    lo = max(0, i - period + 1)
    return float(np.mean(tr[lo:i + 1]))


def classify_trend(h, l, c, n: int, min_move: float) -> str:
    """'up' (HH + HL), 'down' (LH + LL) or 'range'. Uses the last two swing highs and lows.
    A trend is voided if the latest close has already broken the most recent opposite swing."""
    ph, pl = pivot_highs(h, n), pivot_lows(l, n)
    if len(ph) < 2 or len(pl) < 2:
        return "range"
    h1, h2 = h[ph[-2]], h[ph[-1]]
    l1, l2 = l[pl[-2]], l[pl[-1]]
    last = c[-1]
    if h2 - h1 > min_move and l2 - l1 > min_move and last > l2:
        return "up"
    if h1 - h2 > min_move and l1 - l2 > min_move and last < h2:
        return "down"
    return "range"

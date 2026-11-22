"""The strategy, as explicit rules.

  1. D1 structure  -> bias (long / short / none)
  2. D1 candles    -> grade (A = big candles+wicks, B = decent, else skip)
  3. H1 key level  -> a swing high (long) / swing low (short) that price has already broken
                      through, so it is now support / resistance; a zone is "dragged" from it
  4. M5 tap        -> price returns into the zone and does not close through it
  5. M5 CHoCH      -> price breaks the last lower-high (long) / higher-low (short) = structure
                      back in our direction
  6. Stop beyond the M5 structure extreme, target = rr x risk

Everything works on COMPLETED bars only. Shorts reuse the long logic on negated prices.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from .config import Config
from .structure import atr_at, classify_trend, pivot_highs, true_range

M5 = pd.Timedelta(minutes=5)
H1 = pd.Timedelta(hours=1)


@dataclass(frozen=True)
class Bias:
    direction: Optional[str]          # "long" | "short" | None
    grade: Optional[str]              # "A" | "B" | None
    trend: str
    size_ratio: float
    wick_ratio: float
    reason: str


@dataclass(frozen=True)
class Zone:
    id: str
    direction: str
    top: float
    bottom: float
    level: float                      # the broken swing price
    active_from: pd.Timestamp         # H1 close time of the break; M5 bars from here may tap it


@dataclass(frozen=True)
class Signal:
    direction: str
    zone: Zone
    grade: str
    time: pd.Timestamp                # close time of the M5 bar that confirmed the CHoCH
    ref_entry: float                  # close of that bar (actual fill is the next price)
    stop: float
    choch_level: float


# ---------------------------------------------------------------- steps 1-2
def daily_bias(d1: pd.DataFrame, cfg: Config) -> Bias:
    d = d1.tail(max(cfg.d1_lookback_days, cfg.candle_ref_window))
    if len(d) < 30:
        return Bias(None, None, "range", 0.0, 0.0, "not enough daily history")
    h, l, c, o = (d[k].to_numpy(float) for k in ("high", "low", "close", "open"))
    tr = true_range(h, l, c)
    atr_d = atr_at(tr, len(d) - 1, cfg.atr_period)
    trend = classify_trend(h, l, c, cfg.d1_pivot_bars, cfg.d1_trend_min_move_atr * atr_d)
    rng = h - l
    body = np.abs(c - o)
    w = cfg.candle_window
    ref = float(np.median(rng[-cfg.candle_ref_window:]))
    size_ratio = float(rng[-w:].mean() / ref) if ref > 0 else 0.0
    wick_ratio = float(np.mean(np.where(rng[-w:] > 0, (rng[-w:] - body[-w:]) / np.maximum(rng[-w:], 1e-9), 0)))

    if trend == "range":
        return Bias(None, None, trend, size_ratio, wick_ratio, "sideways daily structure")
    direction = "long" if trend == "up" else "short"
    if size_ratio >= cfg.big_size_ratio and wick_ratio >= cfg.big_wick_ratio:
        grade = "A"
    elif size_ratio >= cfg.decent_size_ratio and wick_ratio >= cfg.decent_wick_ratio:
        grade = "B"
    else:
        return Bias(None, None, trend, size_ratio, wick_ratio, "daily candles too small / not enough wick")
    return Bias(direction, grade, trend, size_ratio, wick_ratio, "ok")


# ---------------------------------------------------------------- steps 3-4
def _zone_long(H, L, C, times, tr, cfg: Config):
    """Most recent H1 swing high that a later close has broken above and that is still valid.
    Returns (pivot_idx, break_idx, top, bottom) or None. Operates in 'long' space."""
    n = len(C)
    for i in pivot_highs(H, cfg.h1_pivot_bars)[::-1]:
        P = H[i]
        later = np.nonzero(C[i + 1:] > P)[0]
        if not len(later):
            continue                                   # swing high not broken yet
        b = i + 1 + int(later[0])
        if (n - 1) - b > cfg.zone_max_age_bars:
            continue                                   # stale
        bottom = P - cfg.zone_height_atr * atr_at(tr, i, cfg.atr_period)
        if (C[b + 1:] < bottom).any():
            continue                                   # price already closed through the zone
        return int(i), int(b), float(P), float(bottom)
    return None


def find_zone(h1: pd.DataFrame, direction: str, cfg: Config) -> Optional[Zone]:
    w = h1.tail(cfg.h1_lookback_bars)
    if len(w) < 30:
        return None
    H, L, C = (w[k].to_numpy(float) for k in ("high", "low", "close"))
    tr = true_range(H, L, C)
    times = w.index
    if direction == "long":
        z = _zone_long(H, L, C, times, tr, cfg)
        if z is None:
            return None
        i, b, top, bottom = z
        level = top
    else:  # mirror: negate prices, so a swing LOW becomes a swing high
        z = _zone_long(-L, -H, -C, times, tr, cfg)
        if z is None:
            return None
        i, b, ntop, nbottom = z
        bottom, top = -ntop, -nbottom                  # zone sits above the broken low
        level = bottom
    return Zone(id=f"{direction}:{times[i]}", direction=direction, top=top, bottom=bottom,
                level=level, active_from=times[b] + H1)


# ---------------------------------------------------------------- steps 5-6
def _entry_long(H, L, C, act: int, top: float, bottom: float, buffer: float, cfg: Config):
    """Long-space tap + change-of-character test on the M5 window. Returns (stop, level) or None."""
    N = len(C)
    if act >= N:
        return None
    taps = np.nonzero(L[act:] <= top)[0]               # price has come down into the zone
    if not len(taps):
        return None
    t = act + int(taps[0])
    if (C[t:] < bottom).any():                         # broke through the zone -> not a good zone
        return None
    if (N - 1) - t > cfg.tap_window_bars:              # waited too long for the CHoCH
        return None
    low_idx = t + int(np.argmin(L[t:]))                # the structure low made after the tap
    s = max(0, t - cfg.choch_lookback_bars)
    piv = pivot_highs(H[s:], cfg.m5_pivot_bars) + s
    piv = piv[piv < low_idx]
    if not len(piv):
        return None
    level = float(H[piv[-1]])                          # last lower-high before that low
    if C[N - 1] <= level:                              # no structure break yet
        return None
    if (C[low_idx:N - 1] > level).any():               # the break already happened earlier
        return None
    return float(L[low_idx] - buffer), level


class Strategy:
    """Stateless rules with small caches so a backtest doesn't recompute D1/H1 on every M5 bar."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._bias_key = None
        self._bias: Optional[Bias] = None
        self._zone_key = None
        self._zone: Optional[Zone] = None

    def bias(self, d1: pd.DataFrame) -> Bias:
        key = d1.index[-1]
        if key != self._bias_key:
            self._bias, self._bias_key = daily_bias(d1, self.cfg), key
        return self._bias

    def zone(self, h1: pd.DataFrame, direction: str) -> Optional[Zone]:
        key = (h1.index[-1], direction)
        if key != self._zone_key:
            self._zone, self._zone_key = find_zone(h1, direction, self.cfg), key
        return self._zone

    def in_session(self, bar_close_server_time: pd.Timestamp) -> bool:
        if not self.cfg.use_session_filter:
            return True
        utc = bar_close_server_time - pd.Timedelta(hours=self.cfg.broker_utc_offset_hours)
        return self.cfg.session_start_utc <= utc.hour < self.cfg.session_end_utc

    def evaluate(self, d1: pd.DataFrame, h1: pd.DataFrame, m5: pd.DataFrame,
                 used_zones: Iterable[str] = ()) -> Optional[Signal]:
        """d1/h1/m5 must contain COMPLETED bars only; the last M5 bar is the one that just closed."""
        cfg = self.cfg
        if len(d1) == 0 or len(h1) == 0 or len(m5) == 0:
            return None
        sig_time = m5.index[-1] + M5
        if not self.in_session(sig_time):
            return None
        bias = self.bias(d1)
        if bias.direction is None:
            return None
        zone = self.zone(h1, bias.direction)
        if zone is None or zone.id in set(used_zones):
            return None

        w = m5.tail(cfg.m5_window_bars)
        H, L, C = (w[k].to_numpy(float) for k in ("high", "low", "close"))
        act = int(w.index.searchsorted(zone.active_from))
        buffer = cfg.stop_buffer_atr * atr_at(true_range(H, L, C), len(C) - 1, cfg.atr_period)

        if zone.direction == "long":
            res = _entry_long(H, L, C, act, zone.top, zone.bottom, buffer, cfg)
            if res is None:
                return None
            stop, level = res
        else:
            res = _entry_long(-L, -H, -C, act, -zone.bottom, -zone.top, buffer, cfg)
            if res is None:
                return None
            stop, level = -res[0], -res[1]

        risk = abs(C[-1] - stop)
        if not (cfg.min_stop_price <= risk <= cfg.max_stop_price):
            return None
        if (zone.direction == "long" and stop >= C[-1]) or (zone.direction == "short" and stop <= C[-1]):
            return None
        return Signal(zone.direction, zone, bias.grade, sig_time, float(C[-1]), float(stop), float(level))


def build_levels(direction: str, entry: float, stop: float, rr: float) -> float:
    """Take-profit price for a given actual entry and stop."""
    risk = abs(entry - stop)
    return entry + rr * risk if direction == "long" else entry - rr * risk

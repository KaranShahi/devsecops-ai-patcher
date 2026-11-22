import numpy as np
import pandas as pd
import pytest

from xauusd_bot.config import Config
from xauusd_bot.risk import lots_for_risk
from xauusd_bot.strategy import _entry_long, daily_bias, find_zone
from xauusd_bot.structure import classify_trend, pivot_highs, pivot_lows

CFG = Config()


def test_pivots_need_confirmation_bars():
    h = [1, 2, 5, 3, 2, 1]
    assert list(pivot_highs(h, 2)) == [2]
    assert list(pivot_highs([1, 2, 5], 2)) == []          # not confirmed yet -> no look-ahead
    assert list(pivot_lows([5, 4, 1, 3, 4, 5], 2)) == [2]


def test_flat_equal_highs_give_one_pivot():
    assert list(pivot_highs([1, 3, 3, 1, 0], 1)) == [1]


# ---------------------------------------------------------------- step 1 / 2
def _daily(trend: float, last_range_mult: float = 1.0, body_frac: float = 0.4, n: int = 80):
    t = np.arange(n)
    close = 2000 + trend * t + 12 * np.sin(t / 2.2)
    rng = np.full(n, 20.0)
    rng[-5:] *= last_range_mult
    body = rng * body_frac
    o = close - body / 2
    c = close + body / 2
    h = (o + c) / 2 + rng / 2
    l = (o + c) / 2 - rng / 2
    idx = pd.date_range("2026-01-01", periods=n, freq="1D")
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c}, index=idx)


def test_uptrend_big_candles_is_grade_a_long():
    b = daily_bias(_daily(+1.5, last_range_mult=1.6), CFG)
    assert (b.direction, b.grade, b.trend) == ("long", "A", "up")


def test_downtrend_decent_candles_is_grade_b_short():
    b = daily_bias(_daily(-1.5), CFG)
    assert (b.direction, b.grade) == ("short", "B")


def test_small_candles_are_skipped():
    b = daily_bias(_daily(+1.5, last_range_mult=0.4), CFG)
    assert b.direction is None and "small" in b.reason


def test_sideways_is_skipped():
    b = daily_bias(_daily(0.0), CFG)
    assert b.direction is None


def test_classify_trend_voided_by_break():
    h = np.array([1, 3, 2, 4, 3, 5, 4, 6, 5, 7, 6.0])
    l = h - 1
    c = h - 0.5
    assert classify_trend(h, l, c, 1, 0.0) == "up"
    c[-1] = 0.0                                          # close under the last swing low
    assert classify_trend(h, l, c, 1, 0.0) == "range"
    assert classify_trend(h, l, h - 0.5, 1, 5.0) == "range"   # moves smaller than min_move = sideways


# ---------------------------------------------------------------- steps 3-4
def _h1(highs_lows_closes, start="2026-03-02"):
    a = np.array(highs_lows_closes, float)
    idx = pd.date_range(start, periods=len(a), freq="1h")
    return pd.DataFrame({"open": a[:, 2], "high": a[:, 0], "low": a[:, 1], "close": a[:, 2]}, index=idx)


def _h1_breakout(K=None):
    rows = []
    for i in range(40):                                  # gentle base well below the swing
        rows.append((1000 + i * 0.1 + 1, 1000 + i * 0.1 - 1, 1000 + i * 0.1))
    rows += [(1010, 1004, 1009), (1013, 1008, 1012), (1020, 1011, 1015),   # swing high 1020
             (1016, 1010, 1011), (1014, 1008, 1010), (1013, 1009, 1012),
             (1024, 1012, 1022), (1026, 1020, 1025)]                        # closes above 1020 -> broken
    df = _h1(rows)
    if K is not None:                                    # mirror image around K
        m = df.copy()
        m["high"], m["low"] = K - df["low"], K - df["high"]
        m["open"], m["close"] = K - df["open"], K - df["close"]
        return m
    return df


def test_long_zone_is_the_broken_swing_high():
    z = find_zone(_h1_breakout(), "long", CFG)
    assert z is not None and z.top == 1020 and z.bottom < 1020
    assert z.active_from == pd.Timestamp("2026-03-02") + pd.Timedelta(hours=47)


def test_short_zone_is_exact_mirror_of_long():
    K = 5000
    zl = find_zone(_h1_breakout(), "long", CFG)
    zs = find_zone(_h1_breakout(K), "short", CFG)
    assert zs is not None
    assert zs.bottom == pytest.approx(K - zl.top) and zs.top == pytest.approx(K - zl.bottom)
    assert zs.active_from == zl.active_from


def test_zone_dies_if_price_closes_through_it():
    df = _h1_breakout()
    df.iloc[-1, df.columns.get_loc("close")] = 1000.0
    assert find_zone(df, "long", CFG) is None


def test_unbroken_swing_is_not_a_zone():
    df = _h1_breakout().iloc[:-2]                        # drop the breakout candles
    assert find_zone(df, "long", CFG) is None


# ---------------------------------------------------------------- steps 5-6
def _m5_arrays(extra=()):
    # (high, low, close)
    rows = [(103, 101, 102), (104, 102, 103), (105, 103, 104), (104.5, 102.5, 103), (101.5, 101, 101.2),
            (101.8, 100.8, 101.2),
            (102.5, 100.0, 101.0),    # 6: swing high 102.5 (the "lower high") + TAP of zone top=100
            (101.0, 99.2, 99.7),
            (100.5, 98.8, 99.2),      # 8: structure low
            (100.0, 99.0, 99.8), (100.6, 99.5, 100.4)] + list(extra)
    a = np.array(rows, float)
    return a[:, 0], a[:, 1], a[:, 2]


def test_choch_breaks_last_lower_high_and_stops_below_structure_low():
    H, L, C = _m5_arrays(extra=[(103.0, 100.2, 102.8)])
    stop, level = _entry_long(H, L, C, 0, 100.0, 98.0, 0.1, CFG)
    assert level == 102.5 and stop == pytest.approx(98.7)


def test_no_signal_before_structure_breaks():
    H, L, C = _m5_arrays()
    assert _entry_long(H, L, C, 0, 100.0, 98.0, 0.1, CFG) is None


def test_no_signal_if_zone_was_closed_through():
    H, L, C = _m5_arrays(extra=[(103.0, 100.2, 102.8)])
    C[9] = 97.5                                          # a close below the zone bottom after the tap
    assert _entry_long(H, L, C, 0, 100.0, 98.0, 0.1, CFG) is None


def test_signal_fires_only_on_first_break_bar():
    H, L, C = _m5_arrays(extra=[(103.0, 100.2, 102.8), (104.0, 102.0, 103.5)])
    assert _entry_long(H, L, C, 0, 100.0, 98.0, 0.1, CFG) is None


def test_short_signal_is_exact_mirror_of_long_signal(monkeypatch):
    """Same setup seen upside-down (price -> K - price) must give the mirrored short signal."""
    from dataclasses import replace
    from xauusd_bot.strategy import Bias, Signal, Strategy, Zone

    cfg = replace(CFG, use_session_filter=False)
    H, L, C = _m5_arrays(extra=[(103.0, 100.2, 102.8)])
    idx = pd.date_range("2026-03-02", periods=len(C), freq="5min")
    long_m5 = pd.DataFrame({"open": C, "high": H, "low": L, "close": C}, index=idx)
    K = 500.0
    short_m5 = pd.DataFrame({"open": K - C, "high": K - L, "low": K - H, "close": K - C}, index=idx)
    start = idx[0] - pd.Timedelta(hours=1)
    zl = Zone("long:z", "long", 100.0, 98.0, 100.0, start)
    zs = Zone("short:z", "short", K - 98.0, K - 100.0, K - 100.0, start)

    def run(zone, direction, m5):
        s = Strategy(cfg)
        monkeypatch.setattr(s, "bias", lambda d1: Bias(direction, "A", "x", 1, 1, "ok"))
        monkeypatch.setattr(s, "zone", lambda h1, d: zone)
        dummy = pd.DataFrame({"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0]},
                             index=pd.DatetimeIndex(["2026-01-01"]))
        return s.evaluate(dummy, dummy, m5)

    sl, ss = run(zl, "long", long_m5), run(zs, "short", short_m5)
    assert sl is not None and ss is not None
    assert ss.stop == pytest.approx(K - sl.stop) and ss.choch_level == pytest.approx(K - sl.choch_level)
    assert ss.ref_entry == pytest.approx(K - sl.ref_entry)


# ---------------------------------------------------------------- risk
def test_lot_sizing_rounds_down_and_skips_too_small():
    assert lots_for_risk(10_000, 0.5, 4.5) == 0.11          # 50 / 450 = 0.111
    assert lots_for_risk(100, 0.5, 20.0) == 0.0             # would need 0.00025 lots

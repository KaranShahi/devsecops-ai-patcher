"""Loading / resampling price data. All timestamps are MT5 *server time* (naive)."""
from __future__ import annotations

import pandas as pd

OHLC = ["open", "high", "low", "close"]


def load_csv(path: str) -> pd.DataFrame:
    """Read an M5 file from either `python -m xauusd_bot fetch` or MT5's 'Export Bars' dialog
    (tab/comma separated with <DATE> <TIME> <OPEN> ... headers). Returns OHLC indexed by time."""
    df = pd.read_csv(path, sep=None, engine="python")
    df.columns = [str(c).strip().strip("<>").lower() for c in df.columns]
    if "date" in df.columns and "time" in df.columns:
        ts = pd.to_datetime(df["date"].astype(str) + " " + df["time"].astype(str))
    elif "time" in df.columns:
        ts = pd.to_datetime(df["time"])
    else:
        raise ValueError(f"No time column found in {path}: {list(df.columns)}")
    missing = [c for c in OHLC if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns {missing} in {path}")
    out = df[OHLC].astype(float)
    out.index = pd.DatetimeIndex(ts, name="time")
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out


def resample(m5: pd.DataFrame, rule: str) -> pd.DataFrame:
    """M5 -> H1 ('1h') or D1 ('1D'). Buckets follow server time, matching MT5's own candles."""
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    return m5.resample(rule, label="left", closed="left").agg(agg).dropna()


def fetch_m5_from_mt5(symbol: str, bars: int) -> pd.DataFrame:
    """Pull the most recent `bars` M5 candles from a running MT5 terminal (Windows only)."""
    import MetaTrader5 as mt5  # noqa: PLC0415

    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        mt5.symbol_select(symbol, True)
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, bars)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"No data for {symbol}: {mt5.last_error()}")
        df = pd.DataFrame(rates)
        # MT5 stamps are server-time-as-if-UTC, so this gives server time directly.
        df.index = pd.DatetimeIndex(pd.to_datetime(df["time"], unit="s"), name="time")
        return df[OHLC + ["tick_volume", "spread"]]
    finally:
        mt5.shutdown()

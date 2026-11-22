"""Bar-by-bar backtest of the strategy on M5 data (H1 and D1 are resampled from it).

Execution model (deliberately conservative):
  * signal is confirmed on the close of an M5 bar; the order fills at the NEXT bar's open
  * chart prices are BID; longs fill/exit on bid with the spread added on entry, shorts exit on ask
  * if SL and TP are both inside one bar, the stop is assumed to hit first
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from .config import Config
from .data import resample
from .risk import lots_for_risk
from .strategy import M5, H1, Signal, Strategy, build_levels

D1 = pd.Timedelta(days=1)


@dataclass
class Result:
    trades: pd.DataFrame
    equity: pd.Series
    stats: dict = field(default_factory=dict)


def _stats(trades: pd.DataFrame, equity: pd.Series, start: float) -> dict:
    if trades.empty:
        return {"trades": 0}
    wins, losses = trades[trades.pnl > 0], trades[trades.pnl <= 0]
    gp, gl = wins.pnl.sum(), -losses.pnl.sum()
    dd = (equity / equity.cummax() - 1).min()
    streak = best = 0
    for p in trades.pnl:
        streak = streak + 1 if p <= 0 else 0
        best = max(best, streak)
    return {
        "trades": int(len(trades)),
        "win_rate_pct": round(100 * len(wins) / len(trades), 1),
        "avg_R": round(float(trades.r.mean()), 3),
        "profit_factor": round(float(gp / gl), 2) if gl > 0 else float("inf"),
        "net_pnl": round(float(trades.pnl.sum()), 2),
        "return_pct": round(float(100 * (equity.iloc[-1] / start - 1)), 2),
        "max_drawdown_pct": round(float(100 * dd), 2),
        "longest_losing_streak": int(best),
        "longs": int((trades.direction == "long").sum()),
        "shorts": int((trades.direction == "short").sum()),
    }


def run_backtest(m5: pd.DataFrame, cfg: Config) -> Result:
    h1_all, d1_all = resample(m5, "1h"), resample(m5, "1D")
    h1_end = (h1_all.index + H1).to_numpy()
    d1_end = (d1_all.index + D1).to_numpy()
    strat = Strategy(cfg)

    idx = m5.index
    O, Hh, Ll, Cc = (m5[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    n = len(m5)
    spread = cfg.spread_price

    balance = cfg.start_balance
    used: set[str] = set()
    trades: list[dict] = []
    eq_t, eq_v = [], []
    pos: Optional[dict] = None
    pending: Optional[Signal] = None
    day_key, day_start_bal, day_trades = None, balance, 0

    def close_pos(i: int, price: float, reason: str):
        nonlocal balance, pos
        sign = 1 if pos["direction"] == "long" else -1
        gross = sign * (price - pos["entry"]) * pos["lots"] * cfg.contract_size
        pnl = gross - 2 * cfg.commission_per_lot * pos["lots"]
        balance += pnl
        trades.append({**{k: pos[k] for k in ("direction", "grade", "entry_time", "entry", "stop", "tp", "lots", "zone")},
                       "exit_time": idx[i], "exit": price, "reason": reason, "pnl": pnl,
                       "r": pnl / pos["risk_amt"] if pos["risk_amt"] else 0.0})
        pos = None

    warm = 288 * 40                                    # need ~40 days of M5 history before trading
    for i in range(n):
        t = idx[i]
        key = t.date()
        if key != day_key:
            day_key, day_start_bal, day_trades = key, balance, 0

        # 1) fill a pending order at this bar's open
        if pending is not None and pos is None:
            sg, pending = pending, None
            long = sg.direction == "long"
            entry = O[i] + spread if long else O[i]
            risk = abs(entry - sg.stop)
            valid_side = (sg.stop < entry) if long else (sg.stop > entry)
            if valid_side and cfg.min_stop_price <= risk <= cfg.max_stop_price:
                rp = cfg.risk_pct * (1.0 if sg.grade == "A" else cfg.decent_risk_multiplier)
                lots = lots_for_risk(balance, rp, risk, cfg.contract_size)
                if lots > 0:
                    pos = {"direction": sg.direction, "grade": sg.grade, "entry_time": t, "entry": entry,
                           "stop": sg.stop, "tp": build_levels(sg.direction, entry, sg.stop, cfg.rr),
                           "lots": lots, "zone": sg.zone.id, "risk_amt": risk * lots * cfg.contract_size}
                    used.add(sg.zone.id)
                    day_trades += 1

        # 2) manage the open position on this bar
        if pos is not None:
            if pos["direction"] == "long":
                if O[i] <= pos["stop"]:
                    close_pos(i, O[i], "SL(gap)")
                elif Ll[i] <= pos["stop"]:
                    close_pos(i, pos["stop"], "SL")
                elif Hh[i] >= pos["tp"]:
                    close_pos(i, pos["tp"], "TP")
            else:  # shorts exit on ask = bid + spread
                if O[i] + spread >= pos["stop"]:
                    close_pos(i, O[i] + spread, "SL(gap)")
                elif Hh[i] + spread >= pos["stop"]:
                    close_pos(i, pos["stop"], "SL")
                elif Ll[i] + spread <= pos["tp"]:
                    close_pos(i, pos["tp"], "TP")

        # 3) look for a new signal on the bar that just closed
        if (pos is None and pending is None and i >= warm and i < n - 1
                and day_trades < cfg.max_trades_per_day
                and balance > day_start_bal * (1 - cfg.max_daily_loss_pct / 100)):
            close_t = t + M5
            kd = int(np.searchsorted(d1_end, close_t.to_datetime64(), side="right"))
            kh = int(np.searchsorted(h1_end, close_t.to_datetime64(), side="right"))
            if kd >= 30 and kh >= 30:
                m5w = m5.iloc[max(0, i + 1 - cfg.m5_window_bars): i + 1]
                pending = strat.evaluate(d1_all.iloc[:kd], h1_all.iloc[max(0, kh - cfg.h1_lookback_bars):kh],
                                         m5w, used)

        eq_t.append(t)
        eq_v.append(balance)

    if pos is not None:
        close_pos(n - 1, Cc[-1], "end of data")
        eq_v[-1] = balance

    tdf = pd.DataFrame(trades)
    equity = pd.Series(eq_v, index=pd.DatetimeIndex(eq_t), name="equity")
    return Result(tdf, equity, _stats(tdf, equity, cfg.start_balance))

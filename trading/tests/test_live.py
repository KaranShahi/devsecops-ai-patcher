"""Exercise live.run() against a fake MetaTrader5 module (the real one is Windows-only)."""
import sys
import time
from types import SimpleNamespace as NS

import numpy as np
import pandas as pd
import pytest

from xauusd_bot import live
from xauusd_bot.config import Config
from xauusd_bot.data import resample
from xauusd_bot.strategy import Signal, Strategy, Zone
from xauusd_bot.synth import make_m5


class FakeMT5:
    ACCOUNT_TRADE_MODE_REAL = 2
    TIMEFRAME_M5, TIMEFRAME_H1, TIMEFRAME_D1 = 5, 60, 1440
    DEAL_ENTRY_IN, DEAL_ENTRY_OUT, DEAL_ENTRY_OUT_BY = 0, 1, 3
    TRADE_ACTION_DEAL, ORDER_TYPE_BUY, ORDER_TYPE_SELL, ORDER_TIME_GTC = 1, 0, 1, 0
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2

    def __init__(self, real=False):
        self.real, self.sent, self.checked = real, [], []
        m5 = make_m5(days=60, seed=2)
        self.frames = {5: m5, 60: resample(m5, "1h"), 1440: resample(m5, "1D")}

    def initialize(self): return True
    def shutdown(self): pass
    def last_error(self): return (0, "")
    def account_info(self):
        return NS(login=1, server="Fake", balance=10_000.0, equity=10_000.0, trade_mode=2 if self.real else 0)
    def symbol_select(self, s, b): return True
    def symbol_info(self):  # noqa
        raise NotImplementedError
    def symbol_info_tick(self, s):
        return NS(time=int(time.time()), ask=4000.5, bid=4000.2)
    def positions_get(self, symbol=None): return []
    def history_deals_get(self, a, b): return []
    def copy_rates_from_pos(self, sym, tf, pos, count):
        df = self.frames[tf].tail(count)
        return np.rec.fromarrays([df.index.astype("int64") // 10**9, df.open, df.high, df.low, df.close],
                                 names="time,open,high,low,close")
    def order_check(self, req):
        self.checked.append(req); return NS(retcode=0)
    def order_send(self, req):
        self.sent.append(req); return NS(retcode=10009)


def _install(monkeypatch, tmp_path, fake, force_signal=True):
    fake.symbol_info = lambda s: NS(trade_contract_size=100.0, volume_step=0.01, volume_min=0.01,
                                    volume_max=50.0, digits=2, filling_mode=2)
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake)
    monkeypatch.chdir(tmp_path)
    sleeps = {"n": 0}

    def fake_sleep(_):
        sleeps["n"] += 1
        if sleeps["n"] >= 3:
            (tmp_path / "STOP").write_text("x")

    monkeypatch.setattr(live.time, "sleep", fake_sleep)
    if force_signal:
        zone = Zone("long:test", "long", 4001.0, 3999.0, 4001.0, pd.Timestamp("2026-01-01"))
        sig = Signal("long", zone, "A", pd.Timestamp("2026-03-01"), 4000.5, 3996.0, 4002.0)
        monkeypatch.setattr(Strategy, "evaluate", lambda self, *a, **k: sig)


def test_dry_run_never_sends_orders(monkeypatch, tmp_path):
    fake = FakeMT5()
    _install(monkeypatch, tmp_path, fake)
    live.run(Config(), live=False)
    assert fake.sent == [] and fake.checked == []


def test_live_order_has_sl_tp_and_correct_size(monkeypatch, tmp_path):
    fake = FakeMT5()
    _install(monkeypatch, tmp_path, fake)
    live.run(Config(), live=True)
    assert len(fake.sent) == 1                      # one trade per signal, not one per poll
    r = fake.sent[0]
    assert r["sl"] == 3996.0 and r["tp"] == pytest.approx(4000.5 + 2 * 4.5)
    assert r["volume"] == 0.11 and r["magic"] == Config().magic and r["type"] == fake.ORDER_TYPE_BUY
    assert r["type_filling"] == fake.ORDER_FILLING_IOC
    assert "long:test" in (tmp_path / "bot_state.json").read_text()


def test_real_account_refused_without_flag(monkeypatch, tmp_path):
    fake = FakeMT5(real=True)
    _install(monkeypatch, tmp_path, fake)
    with pytest.raises(SystemExit):
        live.run(Config(), live=True)
    assert fake.sent == []


def test_wide_spread_skips_order(monkeypatch, tmp_path):
    fake = FakeMT5()
    fake.symbol_info_tick = lambda s: NS(time=int(time.time()), ask=4002.0, bid=4000.0)
    _install(monkeypatch, tmp_path, fake)
    live.run(Config(), live=True)
    assert fake.sent == []


def test_loop_runs_real_strategy_without_errors(monkeypatch, tmp_path):
    fake = FakeMT5()
    _install(monkeypatch, tmp_path, fake, force_signal=False)
    live.run(Config(), live=True)                   # plumbing only: real evaluate on synthetic bars

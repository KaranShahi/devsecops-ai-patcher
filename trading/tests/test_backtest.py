import pandas as pd

from xauusd_bot.backtest import run_backtest
from xauusd_bot.config import Config
from xauusd_bot.synth import make_m5


def test_backtest_runs_and_trades_are_sane():
    m5 = make_m5(days=250, seed=3)
    r = run_backtest(m5, Config())
    assert len(r.trades) > 0
    t = r.trades
    long = t.direction == "long"
    assert (t.loc[long, "stop"] < t.loc[long, "entry"]).all()
    assert (t.loc[~long, "stop"] > t.loc[~long, "entry"]).all()
    assert (t.loc[long, "tp"] > t.loc[long, "entry"]).all()
    assert (t.loc[~long, "tp"] < t.loc[~long, "entry"]).all()
    assert (t.exit_time >= t.entry_time).all()
    # TP trades make ~rr x risk, SL trades lose ~1R (before costs rounding)
    assert t.loc[t.reason == "TP", "r"].between(1.9, 2.1).all()
    assert t.loc[t.reason == "SL", "r"].between(-1.05, -0.95).all()
    # at most one trade at a time
    assert (t.entry_time.iloc[1:].to_numpy() >= t.exit_time.iloc[:-1].to_numpy()).all()


def test_no_lookahead_truncating_data_does_not_change_earlier_trades():
    for seed in (3, 5, 8):
        m5 = make_m5(days=250, seed=seed)
        cut = m5.index[int(len(m5) * 0.7)]
        short = run_backtest(m5[m5.index <= cut], Config()).trades
        full = run_backtest(m5, Config()).trades
        if short.empty:
            continue
        done = short[(short.reason != "end of data")]
        f = full[full.exit_time < done.exit_time.max() + pd.Timedelta(seconds=1)].reset_index(drop=True)
        cols = ["direction", "entry_time", "entry", "stop", "exit_time", "exit"]
        pd.testing.assert_frame_equal(done[cols].reset_index(drop=True), f[cols].iloc[:len(done)], check_exact=False)


def test_load_csv_reads_mt5_export_and_fetch_formats(tmp_path):
    from xauusd_bot.data import load_csv
    a = tmp_path / "export.csv"
    a.write_text("<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>\n"
                 "2026.11.20\t16:15:00\t4000.1\t4001.0\t3999.5\t4000.6\t100\t0\t20\n"
                 "2026.11.20\t16:20:00\t4000.6\t4002.0\t4000.2\t4001.9\t90\t0\t20\n")
    df = load_csv(str(a))
    assert list(df.index) == [pd.Timestamp("2026-11-20 16:15"), pd.Timestamp("2026-11-20 16:20")]
    assert df.close.iloc[-1] == 4001.9
    b = tmp_path / "fetched.csv"
    b.write_text("time,open,high,low,close,tick_volume,spread\n2026-11-20 16:15:00,1,2,0.5,1.5,10,3\n")
    assert load_csv(str(b)).high.iloc[0] == 2.0

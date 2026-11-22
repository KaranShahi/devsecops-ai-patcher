from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Config


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="xauusd_bot")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="download recent M5 bars from your MT5 terminal to CSV")
    f.add_argument("--bars", type=int, default=100_000)
    f.add_argument("--out", default="data/xauusd_m5.csv")

    b = sub.add_parser("backtest", help="backtest on an M5 CSV")
    b.add_argument("--data", help="M5 CSV (fetch output or MT5 'Export Bars')")
    b.add_argument("--synthetic", action="store_true", help="use fake data (smoke test only)")
    b.add_argument("--out", default="results")

    l = sub.add_parser("live", help="run against a logged-in MT5 terminal")
    l.add_argument("--live", action="store_true", help="actually send orders (default: dry run)")
    l.add_argument("--allow-real", action="store_true", help="permit a REAL-money account")

    for s in (f, b, l):
        s.add_argument("--config", default=None, help="JSON file overriding defaults")
    a = p.parse_args(argv)
    cfg = Config.from_json(a.config)

    if a.cmd == "fetch":
        from .data import fetch_m5_from_mt5
        df = fetch_m5_from_mt5(cfg.symbol, a.bars)
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(a.out)
        print(f"Saved {len(df)} bars {df.index[0]} -> {df.index[-1]} to {a.out}")
    elif a.cmd == "backtest":
        from .backtest import run_backtest
        from .data import load_csv
        from .synth import make_m5
        if a.synthetic:
            m5 = make_m5(days=250)
        elif a.data:
            m5 = load_csv(a.data)
        else:
            p.error("give --data FILE or --synthetic")
        res = run_backtest(m5, cfg)
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        res.trades.to_csv(out / "trades.csv", index=False)
        res.equity.to_csv(out / "equity.csv")
        print(f"Data: {m5.index[0]} -> {m5.index[-1]} ({len(m5)} M5 bars)")
        for k, v in res.stats.items():
            print(f"  {k:24s} {v}")
        print(f"Trades -> {out/'trades.csv'}")
    elif a.cmd == "live":
        from .live import run
        run(cfg, live=a.live, allow_real=a.allow_real)
    return 0


if __name__ == "__main__":
    sys.exit(main())

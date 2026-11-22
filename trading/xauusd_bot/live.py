"""Live / demo executor for MetaTrader 5 (Windows; `pip install MetaTrader5`).

Safety defaults:
  * DRY RUN unless you pass --live (signals are logged, no orders are sent)
  * refuses a REAL-money account unless you also pass --allow-real
  * every order carries a stop loss and take profit; order_check() runs before order_send()
  * limits: 1 open position, max trades/day, max daily loss, spread filter
  * kill switch: create a file named STOP next to where you run the bot and it halts
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from .config import Config
from .risk import lots_for_risk
from .strategy import Strategy, build_levels

log = logging.getLogger("xauusd_bot")
STATE_FILE = Path("bot_state.json")
STOP_FILE = Path("STOP")


def pick_filling(filling_mode_bits: int, mt5) -> int:
    """Choose an order filling type the symbol supports (bit 1 = FOK, bit 2 = IOC)."""
    if filling_mode_bits & 2:
        return mt5.ORDER_FILLING_IOC
    if filling_mode_bits & 1:
        return mt5.ORDER_FILLING_FOK
    return mt5.ORDER_FILLING_RETURN


def _rates_to_df(rates) -> pd.DataFrame:
    df = pd.DataFrame(rates)
    df.index = pd.DatetimeIndex(pd.to_datetime(df["time"], unit="s"), name="time")  # server time
    return df[["open", "high", "low", "close"]]


def _load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"used_zones": []}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state))


def _today_deals(mt5, cfg: Config):
    """Closed-trade P&L and entry count for today (server day) for this bot's magic number."""
    now = datetime.now() + timedelta(days=1)
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    deals = mt5.history_deals_get(start, now) or []
    server_today = pd.Timestamp(mt5.symbol_info_tick(cfg.symbol).time, unit="s").date()
    pnl, entries = 0.0, 0
    for d in deals:
        if d.magic != cfg.magic or pd.Timestamp(d.time, unit="s").date() != server_today:
            continue
        if d.entry == mt5.DEAL_ENTRY_IN:
            entries += 1
        elif d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY):
            pnl += d.profit + d.commission + d.swap
    return pnl, entries


def run(cfg: Config, live: bool = False, allow_real: bool = False) -> None:
    import MetaTrader5 as mt5  # noqa: PLC0415

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler("bot.log")])
    if not mt5.initialize():
        raise SystemExit(f"MT5 initialize failed: {mt5.last_error()}")
    acct = mt5.account_info()
    if acct is None:
        raise SystemExit("No account info - is the terminal logged in?")
    is_real = acct.trade_mode == mt5.ACCOUNT_TRADE_MODE_REAL
    log.info("Account %s | %s | balance %.2f | mode=%s | %s", acct.login, acct.server, acct.balance,
             "REAL" if is_real else "demo/contest", "LIVE ORDERS" if live else "DRY RUN")
    if live and is_real and not allow_real:
        raise SystemExit("REAL account detected. Re-run with --allow-real only after demo results you trust.")
    if not mt5.symbol_select(cfg.symbol, True):
        raise SystemExit(f"Cannot select symbol {cfg.symbol} (check the exact name in Market Watch)")
    info = mt5.symbol_info(cfg.symbol)

    tick = mt5.symbol_info_tick(cfg.symbol)
    if tick:
        detected = round((tick.time - time.time()) / 3600)
        fresh = abs(tick.time - time.time() - detected * 3600) < 120   # tick is live, not stale
        if fresh and detected != cfg.broker_utc_offset_hours:
            log.warning("Server time looks like UTC%+d but config broker_utc_offset_hours=%+d - fix it, the "
                        "session filter depends on it.", detected, cfg.broker_utc_offset_hours)

    strat = Strategy(cfg)
    state = _load_state()
    last_bar = None
    log.info("Watching %s. Create a file named STOP to halt.", cfg.symbol)

    try:
        while True:
            if STOP_FILE.exists():
                log.warning("STOP file found - exiting.")
                break
            time.sleep(cfg.poll_seconds)

            m5r = mt5.copy_rates_from_pos(cfg.symbol, mt5.TIMEFRAME_M5, 0, cfg.m5_window_bars + 1)
            if m5r is None or len(m5r) < 50:
                continue
            m5 = _rates_to_df(m5r).iloc[:-1]            # drop the still-forming bar
            if m5.index[-1] == last_bar:
                continue
            last_bar = m5.index[-1]

            if [p for p in (mt5.positions_get(symbol=cfg.symbol) or []) if p.magic == cfg.magic]:
                continue                                  # already in a trade
            pnl_today, entries_today = _today_deals(mt5, cfg)
            acct = mt5.account_info()
            if entries_today >= cfg.max_trades_per_day:
                continue
            if pnl_today <= -acct.balance * cfg.max_daily_loss_pct / 100:
                log.warning("Daily loss limit hit (%.2f) - no more trades today.", pnl_today)
                continue

            h1 = _rates_to_df(mt5.copy_rates_from_pos(cfg.symbol, mt5.TIMEFRAME_H1, 0, cfg.h1_lookback_bars + 1)).iloc[:-1]
            d1 = _rates_to_df(mt5.copy_rates_from_pos(cfg.symbol, mt5.TIMEFRAME_D1, 0, 120)).iloc[:-1]
            sig = strat.evaluate(d1, h1, m5, state["used_zones"])
            if sig is None:
                continue

            tick = mt5.symbol_info_tick(cfg.symbol)
            long = sig.direction == "long"
            price = tick.ask if long else tick.bid
            spread = tick.ask - tick.bid
            risk = abs(price - sig.stop)
            tp = build_levels(sig.direction, price, sig.stop, cfg.rr)
            rp = cfg.risk_pct * (1.0 if sig.grade == "A" else cfg.decent_risk_multiplier)
            lots = lots_for_risk(acct.equity, rp, risk, info.trade_contract_size, info.volume_step,
                                 info.volume_min, info.volume_max)
            log.info("SIGNAL %s grade %s | entry~%.2f stop %.2f tp %.2f risk $%.2f/oz spread %.2f lots %.2f | zone %s",
                     sig.direction, sig.grade, price, sig.stop, tp, risk, spread, lots, sig.zone.id)

            if spread > cfg.max_spread_price:
                log.warning("Skipped: spread too wide."); continue
            if not (cfg.min_stop_price <= risk <= cfg.max_stop_price):
                log.warning("Skipped: stop distance outside limits."); continue
            if lots <= 0:
                log.warning("Skipped: minimum lot would risk more than risk_pct."); continue

            state["used_zones"] = (state["used_zones"] + [sig.zone.id])[-200:]
            _save_state(state)
            if not live:
                log.info("DRY RUN - order not sent.")
                continue

            req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": cfg.symbol, "volume": lots,
                   "type": mt5.ORDER_TYPE_BUY if long else mt5.ORDER_TYPE_SELL, "price": price,
                   "sl": round(sig.stop, info.digits), "tp": round(tp, info.digits),
                   "deviation": cfg.deviation_points, "magic": cfg.magic, "comment": "xauusd_bot",
                   "type_time": mt5.ORDER_TIME_GTC, "type_filling": pick_filling(info.filling_mode, mt5)}
            chk = mt5.order_check(req)
            if chk is None or chk.retcode != 0:
                log.error("order_check failed: %s", chk); continue
            res = mt5.order_send(req)
            log.info("order_send -> %s", res)
    finally:
        mt5.shutdown()

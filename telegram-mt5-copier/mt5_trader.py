"""Place MT5 orders for a parsed Signal. Requires Windows + MetaTrader5 terminal."""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone

from signal_parser import Signal

log = logging.getLogger("mt5")

try:
    import MetaTrader5 as mt5
except ImportError:  # lets the parser/tests run on non-Windows machines
    mt5 = None


class Trader:
    def __init__(self, cfg):
        self.t = cfg["trading"]
        self.m = cfg["mt5"]
        self.symbol = self.m["symbol"]
        self.dry = self.t.getboolean("dry_run")
        self.magic = self.t.getint("magic_number")

    # ---------- connection ----------
    def connect(self):
        if mt5 is None:
            if self.dry:
                log.warning("MetaTrader5 package not available - dry-run without MT5.")
                return
            raise RuntimeError("MetaTrader5 package needs Windows + `pip install MetaTrader5`.")
        kwargs = {}
        if self.m.get("login"):
            kwargs = dict(login=int(self.m["login"]), password=self.m["password"], server=self.m["server"])
        if not mt5.initialize(**kwargs):
            raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
        acc = mt5.account_info()
        is_real = acc.trade_mode == mt5.ACCOUNT_TRADE_MODE_REAL
        log.info("Connected: account %s on %s (%s), balance %.2f %s",
                 acc.login, acc.server, "REAL" if is_real else "demo/contest", acc.balance, acc.currency)
        if is_real and not self.t.getboolean("allow_live_account") and not self.dry:
            raise RuntimeError("Real account detected. Set allow_live_account = true to trade it.")
        if not mt5.symbol_select(self.symbol, True):
            raise RuntimeError(f"Symbol {self.symbol} not found at your broker: {mt5.last_error()}")

    def shutdown(self):
        if mt5:
            mt5.shutdown()

    # ---------- helpers ----------
    def _round_lot(self, lots, info):
        step = info.volume_step
        lots = math.floor(lots / step + 1e-9) * step
        return round(max(info.volume_min, min(lots, info.volume_max)), 8)

    def _total_lots(self, sig: Signal, entry: float, info) -> float:
        if self.t["lot_mode"] == "fixed":
            lots = self.t.getfloat("fixed_lots")
        else:
            risk_money = mt5.account_info().balance * self.t.getfloat("risk_percent") / 100
            ticks = abs(entry - sig.sl) / info.trade_tick_size
            loss_per_lot = ticks * info.trade_tick_value
            lots = risk_money / loss_per_lot if loss_per_lot > 0 else info.volume_min
        return min(lots, self.t.getfloat("max_lots_per_signal"))

    def _filling(self, info):
        if info.filling_mode & 2:
            return mt5.ORDER_FILLING_IOC
        if info.filling_mode & 1:
            return mt5.ORDER_FILLING_FOK
        return mt5.ORDER_FILLING_RETURN

    def _open_count(self):
        pos = [p for p in (mt5.positions_get(symbol=self.symbol) or []) if p.magic == self.magic]
        orders = [o for o in (mt5.orders_get(symbol=self.symbol) or []) if o.magic == self.magic]
        return len(pos) + len(orders)

    # ---------- main entry ----------
    def execute(self, sig: Signal) -> list[str]:
        results = []
        if mt5 is None:
            log.info("[DRY-RUN, no MT5] would trade: %s", sig)
            return ["dry-run"]

        info = mt5.symbol_info(self.symbol)
        tick = mt5.symbol_info_tick(self.symbol)
        if not info or not tick:
            raise RuntimeError("No price data for symbol")
        if info.spread > self.t.getint("max_spread_points"):
            log.warning("Skipped: spread %d pts > max %d", info.spread, self.t.getint("max_spread_points"))
            return []

        price_now = tick.ask if sig.side == "BUY" else tick.bid
        tol = self.t.getfloat("market_tolerance")

        # Decide market vs pending
        entry = sig.entry
        if entry is not None and sig.entry_high is not None:  # range: take the better end
            entry = sig.entry if sig.side == "BUY" else sig.entry_high
            if sig.entry <= price_now <= sig.entry_high:
                entry = None  # already inside the range -> market
        if entry is None or abs(price_now - entry) <= tol:
            order_type = mt5.ORDER_TYPE_BUY if sig.side == "BUY" else mt5.ORDER_TYPE_SELL
            action, price = mt5.TRADE_ACTION_DEAL, price_now
        else:
            action, price = mt5.TRADE_ACTION_PENDING, entry
            below = entry < price_now
            if sig.side == "BUY":
                order_type = mt5.ORDER_TYPE_BUY_LIMIT if below else mt5.ORDER_TYPE_BUY_STOP
            else:
                order_type = mt5.ORDER_TYPE_SELL_STOP if below else mt5.ORDER_TYPE_SELL_LIMIT
            if sig.order_kind == "LIMIT" and order_type in (mt5.ORDER_TYPE_BUY_STOP, mt5.ORDER_TYPE_SELL_STOP):
                log.warning("Signal says LIMIT but price is on the wrong side; skipping."); return []

        # Split across TPs
        tps = sig.tps[: self.t.getint("max_tp_orders")]
        total = self._total_lots(sig, price, info)
        per = self._round_lot(total / len(tps), info)
        if per * len(tps) < info.volume_min:  # too small to split
            tps, per = tps[:1], self._round_lot(total, info)

        free = self.t.getint("max_open_trades") - self._open_count()
        if free < len(tps):
            log.warning("Skipped: would exceed max_open_trades (%d slots free, need %d)", free, len(tps))
            return []

        log.info("%s %s x%d @ %s  SL %s  TPs %s  lots/order %s",
                 "MARKET" if action == mt5.TRADE_ACTION_DEAL else "PENDING", sig.side,
                 len(tps), price, sig.sl, tps, per)
        if self.dry:
            log.info("[DRY-RUN] no orders sent.")
            return ["dry-run"]

        for i, tp in enumerate(tps, 1):
            req = {
                "action": action, "symbol": self.symbol, "volume": per, "type": order_type,
                "price": round(price, info.digits), "sl": round(sig.sl, info.digits),
                "tp": round(tp, info.digits), "deviation": self.t.getint("deviation_points"),
                "magic": self.magic, "comment": f"tg-signal TP{i}",
                "type_filling": self._filling(info),
            }
            if action == mt5.TRADE_ACTION_PENDING:
                req["type_filling"] = mt5.ORDER_FILLING_RETURN
                mins = self.t.getint("pending_expiry_minutes")
                if mins:
                    req["type_time"] = mt5.ORDER_TIME_SPECIFIED
                    req["expiration"] = int((datetime.now(timezone.utc) + timedelta(minutes=mins)).timestamp())
            res = mt5.order_send(req)
            ok = res is not None and res.retcode in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED)
            msg = f"TP{i}: {'OK ticket ' + str(res.order) if ok else 'FAILED ' + str(res and (res.retcode, res.comment) or mt5.last_error())}"
            log.log(logging.INFO if ok else logging.ERROR, msg)
            results.append(msg)
        return results

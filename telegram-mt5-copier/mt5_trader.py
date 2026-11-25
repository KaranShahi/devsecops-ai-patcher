"""Place and manage MT5 orders for a parsed Signal. Requires Windows + MetaTrader5 terminal."""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from layers import plan_orders
from signal_parser import Signal

log = logging.getLogger("mt5")
STATE_FILE = Path(__file__).parent / "positions_state.json"

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
        try:
            self.state = json.loads(STATE_FILE.read_text())
        except Exception:
            self.state = {}

    def _save(self):
        STATE_FILE.write_text(json.dumps(self.state))

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

    def _layer_lots(self, entry: float, sl: float, n_layers: int, info) -> float:
        """Lots for ONE layer. Total planned risk is split equally across the planned layers."""
        if self.t["lot_mode"] == "fixed":
            lots = self.t.getfloat("fixed_lots") / n_layers
        else:
            risk_money = mt5.account_info().balance * self.t.getfloat("risk_percent") / 100 / n_layers
            loss_per_lot = abs(entry - sl) / info.trade_tick_size * info.trade_tick_value
            lots = risk_money / loss_per_lot if loss_per_lot > 0 else info.volume_min
        return min(lots, self.t.getfloat("max_lots_per_signal") / n_layers)

    def _filling(self, info):
        if info.filling_mode & 2:
            return mt5.ORDER_FILLING_IOC
        if info.filling_mode & 1:
            return mt5.ORDER_FILLING_FOK
        return mt5.ORDER_FILLING_RETURN

    def _mine(self):
        pos = [p for p in (mt5.positions_get(symbol=self.symbol) or []) if p.magic == self.magic]
        orders = [o for o in (mt5.orders_get(symbol=self.symbol) or []) if o.magic == self.magic]
        return pos, orders

    # ---------- placing a signal ----------
    def execute(self, sig: Signal) -> list[str]:
        spacing = self.t.getfloat("layer_spacing")
        max_layers = self.t.getint("max_layers")
        tol = self.t.getfloat("market_tolerance")
        maxdist = self.t.getfloat("max_entry_distance")

        if mt5 is None:
            orders, notes = plan_orders(sig, (sig.entry or 0), spacing, max_layers, tol, maxdist)
            log.info("[DRY-RUN, no MT5] %s | layers=%s | TPs=%s SL=%s", sig.side, orders, sig.tps, sig.sl)
            return ["dry-run"]

        info = mt5.symbol_info(self.symbol)
        tick = mt5.symbol_info_tick(self.symbol)
        if not info or not tick:
            raise RuntimeError("No price data for symbol")
        if info.spread > self.t.getint("max_spread_points"):
            log.warning("Skipped: spread %d pts > max %d", info.spread, self.t.getint("max_spread_points"))
            return []

        price_now = tick.ask if sig.side == "BUY" else tick.bid
        plan, notes = plan_orders(sig, price_now, spacing, max_layers, tol, maxdist)
        for n in notes:
            log.warning(n)
        if not plan:
            return []

        # Risk is split over the layers we PLAN to place (not extra to cover skipped ones).
        n_planned = len(plan)
        lots = [self._layer_lots(price_now if k == "MARKET" else lvl, sig.sl, n_planned, info) for lvl, k in plan]
        while n_planned > 1 and min(lots) < info.volume_min:   # too small to split that finely
            n_planned -= 1
            plan = plan[:n_planned]
            lots = [self._layer_lots(price_now if k == "MARKET" else lvl, sig.sl, n_planned, info) for lvl, k in plan]
        lots = [self._round_lot(x, info) for x in lots]

        pos, pend = self._mine()
        free = self.t.getint("max_open_trades") - len(pos) - len(pend)
        if free < len(plan):
            log.warning("Skipped: would exceed max_open_trades (%d free, need %d)", free, len(plan))
            return []

        hold = self.t.getint("hold_tp")                       # which TP the runners aim for (0 = last)
        tp_final = sig.tps[min(hold, len(sig.tps)) - 1] if hold > 0 else sig.tps[-1]
        tp1 = sig.tps[0]
        log.info("%s layers %s | lots %s | SL %s | TP1 %s -> manage, final TP %s",
                 sig.side, [(round(l, 2), k) for l, k in plan], lots, sig.sl, tp1, tp_final)
        if self.dry:
            log.info("[DRY-RUN] no orders sent.")
            return ["dry-run"]

        results = []
        buy = sig.side == "BUY"
        for i, ((lvl, kind), vol) in enumerate(zip(plan, lots), 1):
            if kind == "MARKET":
                action, price = mt5.TRADE_ACTION_DEAL, price_now
                otype = mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL
            else:
                action, price = mt5.TRADE_ACTION_PENDING, lvl
                if kind == "LIMIT":
                    otype = mt5.ORDER_TYPE_BUY_LIMIT if buy else mt5.ORDER_TYPE_SELL_LIMIT
                else:
                    otype = mt5.ORDER_TYPE_BUY_STOP if buy else mt5.ORDER_TYPE_SELL_STOP
            req = {
                "action": action, "symbol": self.symbol, "volume": vol, "type": otype,
                "price": round(price, info.digits), "sl": round(sig.sl, info.digits),
                "tp": round(tp_final, info.digits), "deviation": self.t.getint("deviation_points"),
                "magic": self.magic, "comment": f"tg L{i}", "type_filling": self._filling(info),
            }
            if action == mt5.TRADE_ACTION_PENDING:
                req["type_filling"] = mt5.ORDER_FILLING_RETURN
                mins = self.t.getint("pending_expiry_minutes")
                if mins:
                    req["type_time"] = mt5.ORDER_TIME_SPECIFIED
                    req["expiration"] = int((datetime.now(timezone.utc) + timedelta(minutes=mins)).timestamp())
            res = mt5.order_send(req)
            ok = res is not None and res.retcode in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED)
            msg = f"Layer {i} ({kind} @ {price:.2f}, {vol} lots): " + (
                f"OK ticket {res.order}" if ok else f"FAILED {(res.retcode, res.comment) if res else mt5.last_error()}")
            log.log(logging.INFO if ok else logging.ERROR, msg)
            results.append(msg)
            if ok:
                self.state[str(res.order)] = {"tp1": tp1, "buy": buy, "done": False}
        self._save()
        return results

    # ---------- TP1 profit management (run every couple of seconds) ----------
    def manage(self):
        if mt5 is None or self.dry or not self.state:
            return
        action = self.t.get("tp1_action", "partial")          # full | partial | none
        if action == "none":
            return
        info, tick = mt5.symbol_info(self.symbol), mt5.symbol_info_tick(self.symbol)
        if not info or not tick:
            return
        pos, pend = self._mine()
        live = {str(p.identifier) for p in pos} | {str(o.ticket) for o in pend}
        for k in [k for k in self.state if k not in live]:     # closed/expired/cancelled
            del self.state[k]
        buffer = self.t.getfloat("breakeven_buffer")
        pct = self.t.getfloat("tp1_close_percent")
        for p in pos:
            st = self.state.get(str(p.identifier))
            if not st or st["done"]:
                continue
            reached = tick.bid >= st["tp1"] if st["buy"] else tick.ask <= st["tp1"]
            if not reached:
                continue
            log.info("TP1 %.2f reached for position %s (%s)", st["tp1"], p.ticket, action)
            close_vol = p.volume if action == "full" else self._round_lot(p.volume * pct / 100, info)
            if close_vol >= p.volume or p.volume - close_vol < info.volume_min:
                close_vol, full = p.volume, True
            else:
                full = False
            if not self._close(p, close_vol, info, tick):
                continue                                       # retry next cycle
            st["done"] = True
            if not full:
                be = p.price_open + buffer if st["buy"] else p.price_open - buffer
                res = mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "symbol": self.symbol,
                                      "position": p.ticket, "sl": round(be, info.digits), "tp": p.tp})
                log.info("Moved SL to breakeven %.2f: %s", be, res.comment if res else mt5.last_error())
        self._save()

    def _close(self, p, volume, info, tick) -> bool:
        buy = p.type == mt5.POSITION_TYPE_BUY
        res = mt5.order_send({
            "action": mt5.TRADE_ACTION_DEAL, "symbol": self.symbol, "position": p.ticket, "volume": volume,
            "type": mt5.ORDER_TYPE_SELL if buy else mt5.ORDER_TYPE_BUY,
            "price": tick.bid if buy else tick.ask, "deviation": self.t.getint("deviation_points"),
            "magic": self.magic, "comment": "tg close", "type_filling": self._filling(info)})
        ok = res is not None and res.retcode == mt5.TRADE_RETCODE_DONE
        log.log(logging.INFO if ok else logging.ERROR, "Close %s lots of #%s: %s", volume, p.ticket,
                "OK" if ok else (res.comment if res else mt5.last_error()))
        return ok

    # ---------- manager command: "close your worst entry" ----------
    def close_worst_entry(self):
        if mt5 is None or self.dry:
            log.info("[DRY-RUN] would close worst entry")
            return
        pos, _ = self._mine()
        if not pos:
            log.info("Close-worst command received but no open positions.")
            return
        buys = [p for p in pos if p.type == mt5.POSITION_TYPE_BUY]
        sells = [p for p in pos if p.type == mt5.POSITION_TYPE_SELL]
        # worst entry: a sell opened lowest / a buy opened highest (furthest from where we want to be)
        worst = min(sells, key=lambda p: p.price_open) if sells else max(buys, key=lambda p: p.price_open)
        log.info("Closing worst entry: #%s @ %s", worst.ticket, worst.price_open)
        self._close(worst, worst.volume, mt5.symbol_info(self.symbol), mt5.symbol_info_tick(self.symbol))

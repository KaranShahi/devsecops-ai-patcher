"""Pure logic (no MT5): decide which entry layers to place for a signal."""
from __future__ import annotations

from signal_parser import Signal


def layer_levels(sig: Signal, spacing: float, max_layers: int) -> list[float]:
    """Evenly spaced entry levels inside the zone, best price first.

    SELL: start at the bottom of the zone and layer upward (higher = better for a sell).
    BUY:  start at the top of the zone and layer downward.
    Zone 3622-3631 with spacing 3.5 -> 3 layers: 3622, 3626.5, 3631.
    """
    if sig.entry is None:
        return []
    lo, hi = sig.entry, (sig.entry_high or sig.entry)
    if hi <= lo:
        return [lo]
    n = max(1, min(max_layers, int((hi - lo) / spacing) + 1))
    if n == 1:
        return [hi if sig.side == "BUY" else lo]
    step = (hi - lo) / (n - 1)
    levels = [lo + i * step for i in range(n)]
    return levels if sig.side == "SELL" else levels[::-1]


def plan_orders(sig: Signal, price_now: float, spacing: float, max_layers: int,
                tolerance: float, max_distance: float):
    """Return (orders, notes). orders = [(level, 'MARKET'|'LIMIT'|'STOP')]."""
    notes: list[str] = []
    if sig.entry is None:
        return [(price_now, "MARKET")], notes
    lo, hi = sig.entry, (sig.entry_high or sig.entry)
    if price_now < lo - max_distance or price_now > hi + max_distance:
        return [], [f"price {price_now} is more than {max_distance} outside zone {lo}-{hi}; skipped"]

    levels = layer_levels(sig, spacing, max_layers)
    is_zone = hi > lo
    orders = []
    for lvl in levels:
        if abs(lvl - price_now) <= tolerance:
            orders.append((lvl, "MARKET"))
            continue
        better_than_market = (lvl > price_now) if sig.side == "SELL" else (lvl < price_now)
        if better_than_market:
            orders.append((lvl, "LIMIT"))
        elif is_zone:
            notes.append(f"layer {lvl:.2f} already passed by price {price_now}; not chasing")
        else:  # single-price signal on the stop side
            orders.append((lvl, "STOP"))
    if not orders:
        notes.append("price is already beyond the whole zone; no entries placed")
    return orders, notes

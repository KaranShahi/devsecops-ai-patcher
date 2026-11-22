from __future__ import annotations

import math


def lots_for_risk(equity: float, risk_pct: float, stop_distance: float, contract_size: float = 100.0,
                  lot_step: float = 0.01, min_lot: float = 0.01, max_lot: float = 100.0) -> float:
    """Lots so that hitting the stop loses ~risk_pct of equity. Rounds DOWN to the lot step.
    Returns 0.0 when even the minimum lot would risk more than intended (caller should skip)."""
    if stop_distance <= 0 or equity <= 0 or risk_pct <= 0:
        return 0.0
    raw = (equity * risk_pct / 100.0) / (stop_distance * contract_size)
    lots = math.floor(raw / lot_step + 1e-9) * lot_step
    if lots < min_lot:
        return 0.0
    return round(min(lots, max_lot), 8)

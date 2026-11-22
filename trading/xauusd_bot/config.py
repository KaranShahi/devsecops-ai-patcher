"""All tunable settings in one place. Every threshold the strategy uses lives here."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass
class Config:
    symbol: str = "XAUUSD"

    # ---- Step 1: daily market structure -------------------------------------
    d1_pivot_bars: int = 2            # bars each side that define a daily swing high/low
    d1_lookback_days: int = 60
    d1_trend_min_move_atr: float = 0.25  # HH/HL (or LH/LL) must differ by >= this x daily ATR, else "sideways"

    # ---- Step 2: daily candle quality ---------------------------------------
    candle_window: int = 5            # judge the last N completed daily candles
    candle_ref_window: int = 60       # ...against the median daily range over this many days
    big_size_ratio: float = 1.2       # avg range / median range  (>= -> "big" candles)
    decent_size_ratio: float = 0.8    # (>= -> "decent"; below this = "small" -> no trade)
    big_wick_ratio: float = 0.40      # avg (wick / range)  (>= -> "big" wicks)
    decent_wick_ratio: float = 0.25
    decent_risk_multiplier: float = 0.5  # "alright" (grade B) setups risk this fraction of normal

    # ---- Steps 3-4: H1 key level / zone -------------------------------------
    h1_pivot_bars: int = 3
    h1_lookback_bars: int = 200
    atr_period: int = 14
    zone_height_atr: float = 0.5      # zone thickness = this x H1 ATR (measured at the swing)
    zone_max_age_bars: int = 120      # H1 bars after the break before the zone goes stale

    # ---- Steps 5-6: M5 tap + change of character ----------------------------
    m5_window_bars: int = 700
    m5_pivot_bars: int = 2
    tap_window_bars: int = 48         # M5 bars after the tap in which we still wait for the CHoCH
    choch_lookback_bars: int = 24     # M5 bars before the tap to search for the "last lower high"
    stop_buffer_atr: float = 0.25     # stop sits this x M5 ATR beyond the structure extreme

    # ---- Step 7: exits / risk ------------------------------------------------
    rr: float = 2.0
    min_stop_price: float = 1.0       # skip setups whose stop is tighter than this (USD/oz)
    max_stop_price: float = 25.0      # ...or wider than this
    risk_pct: float = 0.5             # % of equity risked per trade (grade A)
    max_trades_per_day: int = 2
    max_daily_loss_pct: float = 2.0   # stop trading for the day once down this much
    max_open_trades: int = 1

    # ---- Session filter (the video trades the New York session) --------------
    use_session_filter: bool = True
    session_start_utc: int = 12
    session_end_utc: int = 20
    broker_utc_offset_hours: int = 2  # MT5 server time minus UTC. CHECK THIS for your broker.

    # ---- Contract / cost model (backtest) ------------------------------------
    contract_size: float = 100.0      # oz per 1.00 lot
    spread_price: float = 0.30        # USD/oz, added on the ask side
    commission_per_lot: float = 0.0   # per side, account currency
    start_balance: float = 10_000.0

    # ---- Live execution -------------------------------------------------------
    magic: int = 26112026
    max_spread_price: float = 0.80
    deviation_points: int = 30
    poll_seconds: int = 5

    @classmethod
    def from_json(cls, path: str | Path | None) -> "Config":
        cfg = cls()
        if path:
            data = json.loads(Path(path).read_text())
            known = {f.name for f in fields(cls)}
            unknown = set(data) - known
            if unknown:
                raise ValueError(f"Unknown config keys: {sorted(unknown)}")
            for k, v in data.items():
                setattr(cfg, k, v)
        return cfg

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

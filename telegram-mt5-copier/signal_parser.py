"""Parse free-text Telegram gold signals into a structured Signal.

Handles formats such as:
    GOLD BUY NOW 2650.5
    XAUUSD SELL @ 2655-2658  SL 2665  TP1 2645 TP2 2640 TP3 2630
    Buy limit gold 2,650  Stop loss: 2640  Take profit: 2670
Anything ambiguous or inconsistent returns None (we never guess on money).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

MAX_SIGNAL_CHARS = 500
NUM = r"(\d{3,5}(?:[.,]\d+)?)"
GOLD_RE = re.compile(r"\b(?:XAU\s*/?\s*USD[A-Z.]*|GOLD)\b")
DIRECTION_RE = re.compile(r"\b(BUY|SELL|LONG|SHORT)\b(?:\s+(LIMIT|STOP))?")
SL_RE = re.compile(r"\b(?:SL|S/L|STOP\s*LOSS|STOPLOSS)\b[\s:@=-]*" + NUM)
TP_RE = re.compile(r"\b(?:TP\s*\d?|T/P\s*\d?|TARGET\s*\d?|TAKE\s*PROFIT\s*\d?)\b[\s:@=-]*" + NUM)
CUT_RE = re.compile(r"\b(?:SL|S/L|STOP\s*LOSS|STOPLOSS|TP\s*\d?|T/P|TARGET|TAKE\s*PROFIT)\b")


@dataclass
class Signal:
    side: str                      # "BUY" | "SELL"
    entry: Optional[float]         # None => market
    entry_high: Optional[float]    # upper end if a range was given
    order_kind: str                # "MARKET" | "LIMIT" | "STOP" | "AUTO"
    sl: float
    tps: list[float] = field(default_factory=list)
    raw: str = ""


def _f(s: str) -> float:
    return float(s.replace(",", "."))


def parse_signal(text: str) -> Optional[Signal]:
    t = re.sub(r"[\u2013\u2014\u2212]", "-", text.upper())  # en/em dash -> hyphen (zones like 3622–3631)
    t = re.sub(r"[^\x00-\x7F]+", " ", t)  # drop emoji
    if len(t) > MAX_SIGNAL_CHARS or "FOR EXAMPLE" in t or "EXAMPLE:" in t:
        return None  # long announcements / worked examples are not live signals
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)         # 2,650 -> 2650
    if not GOLD_RE.search(t):
        return None
    # If a different instrument is named (other than the USD in XAUUSD), skip.
    stripped = GOLD_RE.sub(" ", t)
    if re.search(r"\b(?:EUR|GBP|AUD|NZD|CAD|CHF|JPY|BTC|ETH|US30|NAS100|XAG|SILVER|OIL)[A-Z]{0,3}\b", stripped):
        return None

    d = DIRECTION_RE.search(t)
    if not d:
        return None
    side = "BUY" if d.group(1) in ("BUY", "LONG") else "SELL"
    kind = d.group(2) or "AUTO"

    sl_m = SL_RE.search(t)
    if not sl_m:
        return None  # never trade without a stop loss
    sl = _f(sl_m.group(1))
    tps = [_f(m.group(1)) for m in TP_RE.finditer(t)]
    if not tps:
        return None

    # Entry = number(s) between the direction word and the first SL/TP keyword
    after = t[d.end():]
    cut = CUT_RE.search(after)
    seg = after[: cut.start()] if cut else after
    nums = [_f(n) for n in re.findall(NUM, seg)]
    entry = entry_high = None
    if nums:
        entry = nums[0]
        if len(nums) > 1 and abs(nums[1] - nums[0]) < 50:
            lo, hi = sorted(nums[:2])
            entry, entry_high = lo, hi
    elif "NOW" in seg or "MARKET" in seg or not seg.strip(" @:-"):
        kind = "MARKET"
    if entry is None:
        kind = "MARKET"

    sig = Signal(side, entry, entry_high, kind, sl, tps, text)
    return sig if _consistent(sig) else None


def is_close_worst_command(text: str) -> bool:
    """Manager instruction like 'Close your worst entry' (short messages only)."""
    t = text.upper()
    return len(t) <= 250 and "CLOSE" in t and "WORST" in t and bool(re.search(r"ENTR(Y|IES)|LAYER|POSITION", t))


def _consistent(s: Signal) -> bool:
    if s.entry is None:
        lo = hi = ref = (s.sl + s.tps[0]) / 2
    else:
        lo, hi = s.entry, (s.entry_high or s.entry)
        ref = lo
    if s.side == "BUY":
        ok = s.sl < lo and all(tp > hi for tp in s.tps)
    else:
        ok = s.sl > hi and all(tp < lo for tp in s.tps)
    # gold sanity: stops shouldn't be absurdly far (>150 dollars) from entry
    far = abs(s.sl - ref) > 150 or any(abs(tp - ref) > 400 for tp in s.tps)
    return ok and not far

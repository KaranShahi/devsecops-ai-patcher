import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from signal_parser import parse_signal as p


def test_basic_market():
    s = p("🔥 GOLD BUY NOW 2650.5\nSL 2640\nTP1 2660\nTP2 2670\nTP3 2680")
    assert (s.side, s.entry, s.sl, s.tps, s.order_kind) == ("BUY", 2650.5, 2640, [2660, 2670, 2680], "AUTO")

def test_now_no_price():
    s = p("XAUUSD SELL NOW SL 2665 TP 2640")
    assert s.entry is None and s.order_kind == "MARKET" and s.side == "SELL"

def test_range_and_commas():
    s = p("XAU/USD SELL @ 2,655-2,658 Stop loss: 2,665 Take profit 1: 2,645 Take profit 2: 2,640")
    assert (s.entry, s.entry_high, s.sl, s.tps) == (2655, 2658, 2665, [2645, 2640])

def test_limit():
    assert p("Buy limit gold 2650 SL 2640 TP 2670").order_kind == "LIMIT"

def test_rejects():
    assert p("EURUSD BUY 1.0850 SL 1.08 TP 1.09") is None
    assert p("GOLD BUY 2650 TP 2660") is None            # no SL
    assert p("GOLD BUY 2650 SL 2660 TP 2670") is None    # SL on wrong side
    assert p("Gold is looking bullish today, will buy dips") is None
    assert p("GOLD SELL 2650 SL 2660 TP 2670") is None   # TP on wrong side

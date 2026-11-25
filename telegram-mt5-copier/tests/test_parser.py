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


def test_zone_signal_with_en_dash_and_5_tps():
    s = p("SELL XAUUSD 3622–3631\n\n🎯 TP1: 3619\n🎯 TP2: 3607\n🎯 TP3: 3590\n🎯 TP4: 3550\n🎯 TP5: 3500\n🛑 SL: 3638")
    assert (s.side, s.entry, s.entry_high, s.sl) == ("SELL", 3622, 3631, 3638)
    assert s.tps == [3619, 3607, 3590, 3550, 3500]


def test_long_announcement_is_not_a_signal():
    msg = "IMPORTANT MESSAGE " + "blah " * 120 + "For example, if I send: SELL XAUUSD 3622–3631 TP1: 3619 SL: 3638"
    assert p(msg) is None


def test_close_worst_command():
    from signal_parser import is_close_worst_command as c
    assert c("Close your worst entry now") and not c("hello")
    assert not c("x" * 300 + " close your worst entry")

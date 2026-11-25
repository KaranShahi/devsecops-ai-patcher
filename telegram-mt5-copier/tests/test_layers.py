import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from layers import layer_levels, plan_orders
from signal_parser import parse_signal

SELL = parse_signal("SELL XAUUSD 3622-3631 TP1 3619 TP2 3607 SL 3638")
BUY = parse_signal("BUY XAUUSD 3600-3591 TP1 3610 TP2 3620 SL 3585")


def test_levels():
    assert layer_levels(SELL, 3.5, 3) == [3622, 3626.5, 3631]
    assert layer_levels(BUY, 3.5, 3) == [3600, 3595.5, 3591]
    assert layer_levels(SELL, 3.5, 2) == [3622, 3631]


def test_sell_price_below_zone_places_limits_above():
    orders, _ = plan_orders(SELL, 3620, 3.5, 3, 0.5, 40)
    assert [k for _, k in orders] == ["LIMIT"] * 3


def test_sell_price_inside_zone_does_not_chase():
    orders, notes = plan_orders(SELL, 3626.4, 3.5, 3, 0.5, 40)
    assert [(round(l, 1), k) for l, k in orders] == [(3626.5, "MARKET"), (3631.0, "LIMIT")]
    assert notes  # 3622 layer skipped


def test_price_far_away_skipped():
    assert plan_orders(SELL, 3500, 3.5, 3, 0.5, 40)[0] == []


def test_buy_layers_below_price():
    orders, _ = plan_orders(BUY, 3603, 3.5, 3, 0.5, 40)
    assert [k for _, k in orders] == ["LIMIT"] * 3

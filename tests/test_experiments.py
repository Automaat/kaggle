import main


def test_effective_yield_counts_fertilized_ongoing_crops():
    assert main._effective_yield("MELON") == 6
    assert main._effective_yield("STRAWBERRY") == 8
    assert main._effective_yield("TOMATO") == 8


def test_future_shop_expected_daily_drain():
    expected = main._expected_shop_daily_drain()
    assert expected["WHEAT"] == 3.75
    assert expected["CARROT"] == 2.25
    assert expected["STRAWBERRY"] == 3
    assert expected["MILK"] == 2.25
    assert expected["MELON"] == 0


def test_future_demand_counts_unlock_from_day_three(monkeypatch):
    monkeypatch.setattr(main, "FUTURE_SHOPS", True)
    demand = main._demand_until([], day=2, days=3)
    # One shop is active on days 3 and 4 within the horizon.
    assert demand["WHEAT"] == 3 + 2 * 3.75
    assert demand["MELON"] == 3


def test_integrated_revenue_keeps_floor_inventory_fixed():
    assert main._sale_revenue("MELON", 20_000, 4) == 4
    assert main._advance_inventory("MELON", 20_000, 4) == 20_000


def test_melon_race_fertilizes_first_wave_at_age_five(monkeypatch):
    monkeypatch.setattr(main, "MELON_RACE", True)
    monkeypatch.setattr(main, "_race_active", True)
    tile = {"planted_day": 0, "yield_units": 1, "fertilized_until_day": -1}
    assert main._fertilize_pays(tile, "MELON", age=5, day=5)
    assert not main._fertilize_pays(tile, "MELON", age=6, day=6)


def test_partial_scarcity_sells_only_excess(monkeypatch):
    monkeypatch.setattr(main, "PARTIAL_SCARCITY", True)
    orders = main._sell_orders(
        {"STRAWBERRY": 30}, {"STRAWBERRY": 10_000}, 10_000, 10, 0, 0, 0,
        {"STRAWBERRY": 20}, 0,
    )
    assert orders == [["SELL", "STRAWBERRY", 10]]

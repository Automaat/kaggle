import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

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


def test_carried_wheat_prevents_redundant_purchase(monkeypatch):
    monkeypatch.setattr(main, "CARRIED_FEED", True)
    orders = main._feed_orders(
        herd=4, shed={"WHEAT": 4}, prices={"WHEAT": 25}, money=1_000, carried_wheat=4,
    )
    assert orders == []


def test_supply_accounting_includes_stock_and_own_refertilization(monkeypatch):
    monkeypatch.setattr(main, "SUPPLY_ACCOUNTING", True)
    tile = {
        "kind": "PLANT", "crop": "STRAWBERRY", "planted_day": 0,
        "yield_units": 3, "fertilized_until_day": -1,
    }
    farms = [{"tiles": [[tile]]}, {"tiles": [[None]]}]
    supply = main._supply_forecast(farms, day=10, player=0)
    assert supply["STRAWBERRY"] == 9


def test_crop_batching_waits_until_capacity_would_overflow(monkeypatch):
    monkeypatch.setattr(main, "BATCH_CROP_HARVEST", True)
    tile = {
        "kind": "PLANT", "crop": "STRAWBERRY", "planted_day": 0,
        "yield_units": 2, "watered_today": True, "fertilized_until_day": 12,
    }
    assert ("HARVEST", None) not in main._tile_task(tile, 10, None, {}, {}, {})
    tile["yield_units"] = 4
    assert ("HARVEST", None) in main._tile_task(tile, 12, None, {}, {}, {})


def test_feed_deadline_follows_last_sellable_production(monkeypatch):
    monkeypatch.setattr(main, "FEED_DEADLINE", True)
    cow = {
        "animal": "COW", "placed_day": 1, "fed_today": False,
        "cared_today": False, "yield_units": 0, "fertilizer_available": False,
    }
    assert ("FEED", None) in main._animal_tasks(cow, 28)
    assert ("FEED", None) not in main._animal_tasks(cow, 29)


def test_protected_underfoot_task_belongs_to_later_unit():
    tasks = [(2, 3, 4, ("WATER", None))]
    assert main._protected_underfoot_tasks(tasks, [(0, 0), (3, 4)], [{}, {}]) == {0: 1}

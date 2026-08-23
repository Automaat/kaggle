import copy
import importlib
import pathlib
import sys
from dataclasses import replace

import pytest
from kaggle_environments import make


TOOLS = pathlib.Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))
forecast = importlib.import_module("economics.shop_forecast")
market = importlib.import_module("economics.market_ledger")


def _data(source_step=0, shops=(), terminal_step=718, **values):
    return forecast.ShopForecastInput(
        source_step,
        shops,
        terminal_step,
        **values,
    )


def _product(row, item):
    return row[market.PRODUCTS.index(item)]


def test_future_shop_is_not_observable_before_opening():
    environments = []
    for seed in (1, 2):
        environment = make(
            "kaggriculture",
            configuration={"episodeSteps": 720, "seed": seed},
        )
        environment.run(["pass", "pass"])
        environments.append(environment)
    first = copy.deepcopy(environments[0].steps[0][0].observation)
    second = copy.deepcopy(environments[1].steps[0][0].observation)
    assert "seed" not in first
    assert "seed" not in second
    assert first.town == second.town == {"unlocked_shops": []}
    assert environments[0].steps[72][0].observation.town != (
        environments[1].steps[72][0].observation.town
    )


def test_next_opening_creates_one_branch_per_shop():
    result = forecast.forecast_shops(_data())
    assert len(result.scenarios) == len(market.SHOP_DEMAND)
    assert {scenario.next_shop for scenario in result.scenarios} == set(
        market.SHOP_DEMAND
    )
    assert sum(scenario.probability for scenario in result.scenarios) == 1.0
    assert result.next_shop_replan_step == 72
    assert result.strategy_end_step == 71
    assert forecast.verify_forecast(_data(), result) == ()


def test_rolling_horizons_stop_actions_daily_and_strategy_at_shop():
    result = forecast.forecast_shops(_data(source_step=25, terminal_step=100))
    assert result.next_daily_replan_step == 48
    assert result.next_shop_replan_step == 72
    assert result.action_end_step == 47
    assert result.strategy_end_step == 71
    assert result.investment_end_step == 100


def test_new_shop_consumes_after_market_at_opening_step():
    data = _data(source_step=71, terminal_step=73)
    result = forecast.forecast_shops(data)
    pet = next(
        scenario for scenario in result.scenarios if scenario.next_shop == "PET_CAFE"
    )
    carrot = market.PRODUCTS.index("CARROT")
    initial = tuple(100.0 for _ in market.PRODUCTS)
    before = forecast.inventory_before_steps(initial, pet.drain_by_step)
    assert before[0][carrot] == 100.0
    assert before[1][carrot] == 100.0
    assert before[2][carrot] == 97.0
    assert _product(pet.drain_by_step[1], "CARROT") == 3.0


def test_single_product_and_duplicate_shop_demand_double():
    data = _data(
        source_step=0,
        shops=("PET_CAFE", "PET_CAFE"),
        terminal_step=0,
    )
    result = forecast.forecast_shops(data)
    assert len(result.scenarios) == 1
    assert _product(result.expected_drain_by_step[0], "CARROT") == 5.0
    assert _product(result.expected_drain_by_step[0], "FERTILIZER") == 0.0


def test_eight_open_shops_remove_future_uncertainty():
    shops = tuple(sorted(market.SHOP_DEMAND))
    data = _data(source_step=576, shops=shops)
    result = forecast.forecast_shops(data)
    assert len(result.scenarios) == 1
    assert result.scenarios[0].next_shop is None
    assert result.next_shop_replan_step is None
    assert result.strategy_end_step == 718


def test_shop_signature_ignores_order_and_preserves_duplicates():
    previous = ("PET_CAFE", "YARN_STORE", "PET_CAFE")
    reordered = ("YARN_STORE", "PET_CAFE", "PET_CAFE")
    changed = ("YARN_STORE", "PET_CAFE")
    assert not forecast.needs_shop_replan(previous, reordered)
    assert forecast.needs_shop_replan(previous, changed)


def test_expected_next_shop_demand_conserves_probability():
    data = _data(source_step=71, terminal_step=72)
    result = forecast.forecast_shops(data)
    expected = tuple(
        sum(
            scenario.probability * scenario.drain_by_step[1][index]
            for scenario in result.scenarios
        )
        for index in range(len(market.PRODUCTS))
    )
    assert result.expected_drain_by_step[1] == expected
    assert _product(expected, "FERTILIZER") == 0.0


@pytest.mark.parametrize(
    ("values", "exception"),
    (
        ({"source_step": -1}, ValueError),
        ({"source_step": True}, TypeError),
        ({"shops": ["PET_CAFE"]}, TypeError),
        ({"shops": ("UNKNOWN",)}, ValueError),
        ({"shops": ("PET_CAFE",) * 9}, ValueError),
        ({"max_shops": 9}, ValueError),
        ({"shop_sell_interval_steps": 0}, ValueError),
    ),
)
def test_invalid_input_is_rejected(values, exception):
    arguments = {"source_step": 0, "shops": ()}
    arguments.update(values)
    with pytest.raises(exception):
        _data(**arguments)


def test_inventory_projection_validates_rows():
    initial = (100.0,) * len(market.PRODUCTS)
    with pytest.raises(TypeError):
        forecast.inventory_before_steps(initial, ((1.0,),))
    with pytest.raises(ValueError):
        forecast.inventory_before_steps(
            initial,
            ((-1.0,) * len(market.PRODUCTS),),
        )


def test_verifier_rejects_forged_timing_boundaries():
    data = _data()
    valid = forecast.forecast_shops(data)
    forged = replace(
        valid,
        source_step=718,
        terminal_step=718,
        next_daily_replan_step=None,
        next_shop_replan_step=None,
        action_end_step=718,
        strategy_end_step=718,
    )
    assert forecast.verify_forecast(data, forged) == (
        "source step mismatch",
        "daily replan boundary mismatch",
        "shop replan boundary mismatch",
        "action horizon mismatch",
        "strategy horizon mismatch",
    )

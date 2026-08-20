"""The agent hand-derives crop tables from the rules; pin them to the simulator."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest
from kaggle_environments.envs.kaggriculture import kaggriculture as env

import main


@pytest.mark.parametrize("crop", sorted(main.CROPS))
def test_crop_constants_match(crop):
    assert main.CROPS[crop]["seed"] == env.CROPS[crop]["seed"]
    assert main.CROPS[crop]["first_yield_day"] == env.CROPS[crop]["first_yield_day"]
    assert main.CROPS[crop]["max_yield_day"] == env.CROPS[crop]["max_yield_day"]
    assert main.CROPS[crop]["max_yield"] == env.CROPS[crop]["max_yield"]
    assert main.CROPS[crop]["ongoing"] == env.CROPS[crop]["ongoing"]


@pytest.mark.parametrize("crop", sorted(main.PRODUCTION_AGES))
def test_production_ages_match(crop):
    """Ongoing crops produce at first_yield_day, then every `interval` days."""
    data = env.CROPS[crop]
    expected = [data["first_yield_day"] + k * data["interval"] for k in range(data["max_yield"])]
    assert main.PRODUCTION_AGES[crop] == expected


@pytest.mark.parametrize("animal", sorted(main.ANIMALS))
def test_animal_constants_match(animal):
    assert main.ANIMALS[animal]["cost"] == env.ANIMALS[animal]["cost"]
    assert main.ANIMALS[animal]["structure"] == env.ANIMALS[animal]["structure"]
    assert main.ANIMALS[animal]["product"] == env.ANIMALS[animal]["product"]
    assert main.ANIMALS[animal]["first_yield_day"] == env.ANIMALS[animal]["first_yield_day"]
    # CARE banks one unit per fed day and pays out on the next production, so
    # the steady rate is (1 + interval) units every `interval` days.
    interval = env.ANIMALS[animal]["interval"]
    assert main.ANIMALS[animal]["interval"] == interval
    assert main.ANIMALS[animal]["max_held"] == env.ANIMALS[animal]["max_held"]
    assert main.ANIMALS[animal]["rate"] == pytest.approx((1 + interval) / interval)


def test_shops_match():
    assert main.SHOPS == env.SHOPS


def test_shop_ticks_per_day():
    assert main.SHOP_TICKS_PER_DAY == 24 // 4  # turnsPerDay / townShopSellInterval


def test_market_params_override_is_honoured():
    """A configuration override must beat our frozen copy of the curve."""
    override = env._resolve_market_params({"WOOL": {"above_target": 0.95}})
    inventory = main.MARKET_I0 + 50
    assert main.market_price("WOOL", inventory, override) == env.market_price("WOOL", inventory, override)
    assert main.market_price("WOOL", inventory, override) != main.market_price("WOOL", inventory)

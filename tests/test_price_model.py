"""The agent prices sales locally; that copy must track the environment exactly."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest
from kaggle_environments.envs.kaggriculture import kaggriculture as env

import main

OFFSETS = [-3000, -900, -450, -100, -1, 0, 1, 100, 450, 900, 3000, 20000]


@pytest.mark.parametrize("item", sorted(main.MARKET_PARAMS))
@pytest.mark.parametrize("offset", OFFSETS)
def test_price_matches_environment(item, offset):
    inventory = main.MARKET_I0 + offset
    assert main.market_price(item, inventory) == env.market_price(item, inventory)

import copy
import json
import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))

from runner import load_agent


ROOT = pathlib.Path(__file__).resolve().parent.parent
CANDIDATE = ROOT / "agents_2.0.x/round42_7_age_aware_herd"
REPLAY = ROOT / "replays/main_vs_champion_42.json"


def _observation():
    replay = json.loads(REPLAY.read_text())
    return copy.deepcopy(replay["steps"][0][0]["observation"])


def _policy():
    loaded = load_agent(str(CANDIDATE))
    return loaded.module._policy_agent.policy.baseline


def test_existing_product_units_distinguish_placed_held_and_modeled():
    policy = _policy()
    observation = _observation()
    observation["day"] = 10
    observation["farms"][observation["player"]]["tiles"][0][0] = {
        "kind": "PASTURE",
        "animal": "COW",
        "placed_day": 0,
        "yield_units": 3,
        "pending_care_bonus": 2,
    }
    observation["private"]["shed"]["COW"] = 1

    units = policy._existing_product_units(
        policy.module, observation, "COW", Counter(COW=3), 17, 0.8,
    )

    assert units == 61


def test_existing_product_units_include_phase_day_29():
    policy = _policy()
    observation = _observation()
    observation["day"] = 28
    observation["farms"][observation["player"]]["tiles"][0][0] = {
        "kind": "PASTURE",
        "animal": "COW",
        "placed_day": 1,
        "yield_units": 0,
        "pending_care_bonus": 0,
    }

    units = policy._existing_product_units(
        policy.module, observation, "COW", Counter(COW=1), 0, 1.0,
    )

    assert units == 2


def test_existing_product_units_keep_harvestable_yield_exact():
    policy = _policy()
    observation = _observation()
    observation["day"] = 29
    observation["farms"][observation["player"]]["tiles"][0][0] = {
        "kind": "PASTURE",
        "animal": "COW",
        "placed_day": 1,
        "yield_units": 3,
        "pending_care_bonus": 0,
    }

    units = policy._existing_product_units(
        policy.module, observation, "COW", Counter(COW=1), 0, 0.1,
    )

    assert units == 3


def test_age_aware_flag_changes_existing_animal_margin(monkeypatch):
    policy = _policy()
    observation = _observation()
    observation["day"] = 10
    observation["farms"][observation["player"]]["tiles"][0][0] = {
        "kind": "PASTURE",
        "animal": "COW",
        "placed_day": 0,
        "yield_units": 3,
        "pending_care_bonus": 2,
    }
    counts = Counter(COW=1)

    fixed = policy._animal_margin(policy.module, observation, "COW", counts)
    monkeypatch.setenv("AGENT2_AGE_AWARE_HERD", "1")
    aware = policy._animal_margin(policy.module, observation, "COW", counts)

    assert aware != fixed


def test_private_product_stock_reduces_margin(monkeypatch):
    policy = _policy()
    observation = _observation()
    observation["private"]["shed"]["MILK"] = 20

    ignored = policy._animal_margin(policy.module, observation, "COW", Counter())
    monkeypatch.setenv("AGENT2_PRIVATE_PRODUCT_STOCK", "1")
    included = policy._animal_margin(policy.module, observation, "COW", Counter())

    assert included < ignored

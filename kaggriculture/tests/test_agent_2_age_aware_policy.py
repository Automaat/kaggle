import copy
import json
import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))

from runner import load_agent, run_match
from variants import _environment


ROOT = pathlib.Path(__file__).resolve().parent.parent
CANDIDATE = ROOT / "agents_2.0.x/round42_8_age_aware_policy"
CONTROL = ROOT / "agents_2.0.x/round42_1_dynamic_herd"
RELEASE = ROOT / "agents_1.0.x/v1_17_0_age_aware_herd"
REPLAY = ROOT / "replays/main_vs_champion_42.json"


def _observation():
    replay = json.loads(REPLAY.read_text())
    return copy.deepcopy(replay["steps"][0][0]["observation"])


def _policy():
    loaded = load_agent(str(CANDIDATE))
    return loaded.module._policy_agent.policy.baseline


def test_stockless_saturation_keeps_future_supply_only():
    policy = _policy()
    observation = _observation()
    observation["day"] = 10
    observation["farms"][observation["player"]]["tiles"][0][0] = {
        "kind": "PASTURE",
        "animal": "COW",
        "placed_day": 0,
        "yield_units": 8,
        "pending_care_bonus": 2,
    }
    counts = Counter(COW=1)
    full = policy._existing_product_units(
        policy.module, observation, "COW", counts, 17, 0.8,
    )
    stockless = policy._saturation_product_units(
        policy.module, observation, "COW", counts, 17, 0.8, "stockless",
    )
    assert stockless == full - 8


def test_overlap_saturation_is_bounded_by_stockless():
    policy = _policy()
    observation = _observation()
    observation["day"] = 10
    observation["farms"][observation["player"]]["tiles"][0][0] = {
        "kind": "PASTURE",
        "animal": "COW",
        "placed_day": 0,
        "yield_units": 8,
        "pending_care_bonus": 2,
    }
    counts = Counter(COW=1)
    stockless = policy._saturation_product_units(
        policy.module, observation, "COW", counts, 17, 0.8, "stockless",
    )
    overlap = policy._saturation_product_units(
        policy.module, observation, "COW", counts, 17, 0.8, "overlap",
    )
    assert 0 <= overlap < stockless


def test_cow_floor_overrides_age_aware_argmax():
    values = {
        "AGENT2_AGE_AWARE_HERD": "1",
        "AGENT2_AGE_AWARE_COW_FLOOR": "2",
        "AGENT2_COW_REALIZATION": "0.01",
        "AGENT2_SHEEP_REALIZATION": "1",
        "AGENT2_DYNAMIC_ANIMALS": "COW,SHEEP",
    }
    with _environment(values):
        policy = _policy()
        policy._set_dynamic_herd(_observation())
    assert Counter(policy._dynamic_herd)["COW"] >= 2


def test_age_aware_policy_finishes_live_episode(monkeypatch):
    monkeypatch.setenv("AGENT2_AGE_AWARE_HERD", "1")
    monkeypatch.setenv("AGENT2_AGE_AWARE_SATURATION", "overlap")
    _, _, statuses = run_match(str(CANDIDATE), str(CONTROL), seed=68)
    assert statuses == ["DONE", "DONE"]


def test_release_enables_stockless_age_aware_margin(monkeypatch):
    loaded = load_agent(str(RELEASE))
    policy = loaded.module._policy_agent.policy.baseline
    observation = _observation()
    observation["day"] = 10
    observation["farms"][observation["player"]]["tiles"][0][0] = {
        "kind": "PASTURE",
        "animal": "COW",
        "placed_day": 0,
        "yield_units": 8,
        "pending_care_bonus": 2,
    }
    counts = Counter(COW=1)

    default = policy._animal_margin(policy.module, observation, "COW", counts)
    monkeypatch.setenv("AGENT2_AGE_AWARE_SATURATION", "full")
    full = policy._animal_margin(policy.module, observation, "COW", counts)
    monkeypatch.setenv("AGENT2_AGE_AWARE_HERD", "0")
    legacy = policy._animal_margin(policy.module, observation, "COW", counts)

    assert default != full
    assert default != legacy

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))

from runner import load_agent, run_match
from variants import _environment


ROOT = pathlib.Path(__file__).resolve().parent.parent
CANDIDATE = ROOT / "agents_2.0.x/round42_6_land_priority"


def _policy(values):
    with _environment(values):
        loaded = load_agent(str(CANDIDATE))
    return loaded.module._policy_agent.policy.baseline


def test_land_priority_holds_animal_order_near_gate():
    policy = _policy({"AGENT2_LAND_PRIORITY": "1"})
    tiles = [
        [None if x < 5 and y < 5 else "LOCKED" for x in range(10)]
        for y in range(10)
    ]
    farm = {
        "money": 4700,
        "tiles": tiles,
        "unlocked_quadrants": ["NW"],
    }
    obs = {"player": 0, "day": 9, "farms": [farm]}
    action = {"market": [["SELL", "MELON", 2], ["BUY_ANIMAL", "COW", 1]]}
    with _environment({"AGENT2_LAND_PRIORITY": "1"}):
        policy._hold_animal_cash_for_land(obs, action)
    assert action["market"] == [["SELL", "MELON", 2]]


def test_land_priority_keeps_animal_order_far_from_gate():
    policy = _policy({"AGENT2_LAND_PRIORITY": "1"})
    tiles = [
        [None if x < 5 and y < 5 else "LOCKED" for x in range(10)]
        for y in range(10)
    ]
    farm = {
        "money": 100,
        "tiles": tiles,
        "unlocked_quadrants": ["NW"],
    }
    obs = {"player": 0, "day": 9, "farms": [farm]}
    action = {"market": [["BUY_ANIMAL", "COW", 1]]}
    with _environment({"AGENT2_LAND_PRIORITY": "1"}):
        policy._hold_animal_cash_for_land(obs, action)
    assert action["market"] == [["BUY_ANIMAL", "COW", 1]]


def test_land_priority_finishes_live_episode(monkeypatch):
    monkeypatch.setenv("AGENT2_DYNAMIC_HERD", "1")
    monkeypatch.setenv("AGENT2_LAND_PRIORITY", "1")
    _, _, statuses = run_match(str(CANDIDATE), "champion", seed=67)
    assert statuses == ["DONE", "DONE"]

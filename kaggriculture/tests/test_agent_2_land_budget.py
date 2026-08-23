import copy
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))

from runner import load_agent
from variants import _environment


ROOT = pathlib.Path(__file__).resolve().parent.parent
CANDIDATE = ROOT / "agents_2.0.x/round42_2_land_budget"
REPLAY = ROOT / "replays/main_vs_champion_42.json"


def _observation():
    replay = json.loads(REPLAY.read_text())
    return copy.deepcopy(replay["steps"][0][0]["observation"])


def _policy(values=None):
    with _environment(values or {}):
        loaded = load_agent(str(CANDIDATE))
    return loaded.module._policy_agent.policy.baseline


def test_sale_funded_land_is_disabled_by_default():
    policy = _policy()
    observation = _observation()
    farm = observation["farms"][observation["player"]]
    farm["money"] = 4000
    action = {"market": [["SELL", "MELON", 10]]}
    policy._add_sale_funded_land(observation, action)
    assert ["BUY_LAND"] not in action["market"]


def test_sale_funded_land_uses_executable_sales_and_existing_gate():
    policy = _policy({"AGENT2_SALE_FUNDED_LAND": "1"})
    observation = _observation()
    farm = observation["farms"][observation["player"]]
    farm["money"] = 4000
    action = {"market": [["SELL", "MELON", 10], ["HIRE"]]}
    with _environment({"AGENT2_SALE_FUNDED_LAND": "1"}):
        policy._add_sale_funded_land(observation, action)
    assert action["market"][:2] == [["SELL", "MELON", 10], ["BUY_LAND"]]


def test_sale_funded_land_does_not_bypass_cash_gate():
    policy = _policy({"AGENT2_SALE_FUNDED_LAND": "1"})
    observation = _observation()
    farm = observation["farms"][observation["player"]]
    farm["money"] = 100
    action = {"market": [["SELL", "WHEAT", 1]]}
    with _environment({"AGENT2_SALE_FUNDED_LAND": "1"}):
        policy._add_sale_funded_land(observation, action)
    assert ["BUY_LAND"] not in action["market"]


def test_short_seed_reserve_only_covers_horizon_capacity():
    policy = _policy({
        "AGENT2_SHORT_SEED_RESERVE": "1",
        "AGENT2_SEED_RESERVE_HORIZON": "1",
    })
    observation = _observation()
    farm = observation["farms"][observation["player"]]
    farm["money"] = 3000
    policy._observation = observation
    with _environment({
        "AGENT2_SHORT_SEED_RESERVE": "1",
        "AGENT2_SEED_RESERVE_HORIZON": "1",
    }):
        orders = policy.module._land_orders(farm, 0, 25)
    assert orders == [["BUY_LAND"]]


def test_short_seed_reserve_preserves_payback_gate():
    policy = _policy({"AGENT2_SHORT_SEED_RESERVE": "1"})
    observation = _observation()
    farm = observation["farms"][observation["player"]]
    farm["money"] = 100000
    policy._observation = observation
    with _environment({"AGENT2_SHORT_SEED_RESERVE": "1"}):
        orders = policy.module._land_orders(farm, 22, 25)
    assert orders == []


def test_post_land_hiring_uses_unlocked_land_after_purchase():
    policy = _policy({
        "AGENT2_POST_LAND_HIRING": "1",
        "AGENT2_MAX_HANDS": "20",
    })
    observation = _observation()
    action = {"market": [["BUY_LAND"], *[["HIRE"]] * 5]}
    with _environment({"AGENT2_POST_LAND_HIRING": "1"}):
        policy._set_post_land_hires(observation, action)
    assert action["market"].count(["HIRE"]) == 10

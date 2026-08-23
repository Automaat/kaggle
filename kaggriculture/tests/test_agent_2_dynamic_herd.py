import copy
import json
import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))

from runner import load_agent, run_match
from variants import _environment


ROOT = pathlib.Path(__file__).resolve().parent.parent
CANDIDATE = ROOT / "agents_2.0.x/round42_1_dynamic_herd"
REPLAY = ROOT / "replays/main_vs_champion_42.json"


def _observation():
    replay = json.loads(REPLAY.read_text())
    return replay["steps"][0][0]["observation"]


def test_yarn_store_increases_sheep_margin():
    loaded = load_agent(str(CANDIDATE))
    policy = loaded.module._policy_agent.policy.baseline
    observation = _observation()
    base = policy._animal_margin(policy.module, observation, "SHEEP", Counter())
    demand = copy.deepcopy(observation)
    demand["town"]["unlocked_shops"] = ["YARN_STORE"]
    boosted = policy._animal_margin(policy.module, demand, "SHEEP", Counter())
    assert boosted > base


def test_scarce_eggs_admit_geese():
    values = {
        "AGENT2_DYNAMIC_HERD": "1",
        "AGENT2_ANIMAL_MARGIN": "-1000000",
    }
    observation = _observation()
    observation["market"]["inventory"]["EGG"] = 9000
    observation["town"]["unlocked_shops"] = ["BAKERY", "BRUNCH_SPOT"]
    with _environment(values):
        loaded = load_agent(str(CANDIDATE))
        policy = loaded.module._policy_agent.policy.baseline
        policy._set_dynamic_herd(observation)
    assert "GOOSE" in policy._dynamic_herd


def test_dynamic_herd_respects_work_limit():
    values = {
        "AGENT2_DYNAMIC_HERD": "1",
        "AGENT2_DAILY_WORK_BUDGET": "84",
        "AGENT2_PLANT_CAP_RELEASE": "48",
        "AGENT2_ANIMAL_WORK": "3",
        "AGENT2_ANIMAL_MARGIN": "-1000000",
    }
    with _environment(values):
        loaded = load_agent(str(CANDIDATE))
        policy = loaded.module._policy_agent.policy.baseline
        policy._set_dynamic_herd(_observation())
    assert len(policy._dynamic_herd) == 12
    assert "GOOSE" not in policy._dynamic_herd


def test_dynamic_herd_rejects_negative_margin():
    values = {
        "AGENT2_DYNAMIC_HERD": "1",
        "AGENT2_DYNAMIC_HERD_MIN": "0",
        "AGENT2_ANIMAL_MARGIN": "1000000",
    }
    with _environment(values):
        loaded = load_agent(str(CANDIDATE))
        policy = loaded.module._policy_agent.policy.baseline
        policy._set_dynamic_herd(_observation())
    assert policy._dynamic_herd == []
    assert policy.module.HERD_SPEC == ""


def test_dynamic_herd_finishes_live_episode(monkeypatch):
    monkeypatch.setenv("AGENT2_DYNAMIC_HERD", "1")
    _, _, statuses = run_match(str(CANDIDATE), "champion", seed=64)
    assert statuses == ["DONE", "DONE"]

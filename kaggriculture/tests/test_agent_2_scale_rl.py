import copy
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))

from runner import load_agent, run_match


ROOT = pathlib.Path(__file__).resolve().parent.parent
CANDIDATE = ROOT / "agents_2.0.x/round37_6_scale_rl"
BASELINE = "variant:KAGG_LAND=2;KAGG_MAX_HANDS=14;KAGG_HANDS_PER_TILE=0.2"
REPLAY = ROOT / "replays/main_vs_champion_42.json"


def _baseline_module(loaded):
    return loaded.module._policy_agent.policy.baseline.module


def test_scale_policy_has_twenty_six_features():
    loaded = load_agent(str(CANDIDATE))
    replay = json.loads(REPLAY.read_text())
    loaded(copy.deepcopy(replay["steps"][48][0]["observation"]))
    module = _baseline_module(loaded)
    assert len(module.ROUTE_RL_WEIGHTS) == 26


def test_scale_policy_finishes_live_episode():
    _, _, statuses = run_match(str(CANDIDATE), BASELINE, seed=42)
    assert statuses == ["DONE", "DONE"]

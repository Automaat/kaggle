import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))

from runner import load_agent, run_match


ROOT = pathlib.Path(__file__).resolve().parent.parent
CANDIDATE = ROOT / "agents_2.0.x/round42_4_marginal_field"
REPLAY = ROOT / "replays/main_vs_champion_42.json"


def _observation():
    replay = json.loads(REPLAY.read_text())
    return replay["steps"][432][0]["observation"]


def test_marginal_field_keeps_base_when_work_is_expensive(monkeypatch):
    monkeypatch.setenv("AGENT2_MARGINAL_FIELD", "1")
    monkeypatch.setenv("AGENT2_FIELD_WORK_PRICE", "100000")
    loaded = load_agent(str(CANDIDATE))
    policy = loaded.module._policy_agent.policy.baseline
    observation = _observation()
    farm = observation["farms"][observation["player"]]
    tiles = list(policy.module._my_tiles(farm))
    plan = policy.module._dynamic_plan(
        tiles,
        observation["day"],
        observation["market"]["inventory"],
        observation["town"]["unlocked_shops"],
        len(farm["tiles"]),
    )
    cap = policy._marginal_plant_cap(
        policy.module,
        plan,
        tiles,
        observation["day"],
        observation["market"]["inventory"],
        observation["town"]["unlocked_shops"],
        len(farm["tiles"]),
        48,
    )
    assert cap == 48


def test_marginal_field_finishes_live_episode(monkeypatch):
    monkeypatch.setenv("AGENT2_DYNAMIC_HERD", "1")
    monkeypatch.setenv("AGENT2_MARGINAL_FIELD", "1")
    _, _, statuses = run_match(str(CANDIDATE), "champion", seed=65)
    assert statuses == ["DONE", "DONE"]

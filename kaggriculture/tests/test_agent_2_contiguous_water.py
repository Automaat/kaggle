import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))

from runner import load_agent, run_match


ROOT = pathlib.Path(__file__).resolve().parent.parent
CANDIDATE = ROOT / "agents_2.0.x/round38_1_contiguous_water"
BASELINE = "variant:KAGG_LAND=2;KAGG_MAX_HANDS=14;KAGG_HANDS_PER_TILE=0.2"


def _plant():
    return {
        "kind": "PLANT",
        "crop": "MELON",
        "planted_day": 0,
        "yield_units": 1,
    }


def test_safe_water_uses_contiguous_route_halves():
    loaded = load_agent(str(CANDIDATE))
    baseline = loaded.module._policy_agent.policy.baseline
    board = [[None for _x in range(10)] for _y in range(10)]
    board[0][0] = _plant()
    board[9][0] = _plant()
    farm = {"tiles": board, "unlocked_quadrants": ["NW", "NE", "SW", "SE"]}
    baseline._observation = {"player": 0, "day": 0, "farms": [farm, farm]}
    assert not baseline._keep_task(baseline.module, (2, 0, 0, ("WATER", None)))
    assert baseline._keep_task(baseline.module, (2, 0, 9, ("WATER", None)))


def test_contiguous_water_finishes_live_episode():
    _, _, statuses = run_match(str(CANDIDATE), BASELINE, seed=42)
    assert statuses == ["DONE", "DONE"]

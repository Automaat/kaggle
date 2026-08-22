import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))

from runner import load_agent, run_match


ROOT = pathlib.Path(__file__).resolve().parent.parent
CANDIDATE = ROOT / "agents_2.0.x/round37_5_water_cohorts"
BASELINE = "variant:KAGG_LAND=2;KAGG_MAX_HANDS=14;KAGG_HANDS_PER_TILE=0.2"


def _policy(loaded):
    return loaded.module._policy_agent.policy


def _observation(tile, day):
    board = [[None for _x in range(10)] for _y in range(10)]
    board[0][0] = tile
    return {"player": 0, "day": day, "farms": [{"tiles": board}, {"tiles": board}]}


def test_safe_water_uses_stable_day_parity():
    loaded = load_agent(str(CANDIDATE))
    baseline = _policy(loaded).baseline
    tile = {
        "kind": "PLANT",
        "crop": "MELON",
        "planted_day": 0,
        "yield_units": 1,
    }
    baseline._observation = _observation(tile, 2)
    task = (2, 0, 0, ("WATER", None))
    assert baseline._keep_task(baseline.module, task)
    baseline._observation = _observation(tile, 3)
    assert not baseline._keep_task(baseline.module, task)


def test_yield_water_is_never_skipped():
    loaded = load_agent(str(CANDIDATE))
    baseline = _policy(loaded).baseline
    tile = {
        "kind": "PLANT",
        "crop": "MELON",
        "planted_day": 0,
        "yield_units": 1,
    }
    baseline._observation = _observation(tile, 6)
    task = (2, 0, 0, ("WATER", None))
    assert baseline._keep_task(baseline.module, task)


def test_urgent_water_is_never_filtered():
    loaded = load_agent(str(CANDIDATE))
    baseline = _policy(loaded).baseline
    tile = {
        "kind": "PLANT",
        "crop": "MELON",
        "planted_day": 0,
        "yield_units": 1,
    }
    baseline._observation = _observation(tile, 3)
    task = (0, 0, 0, ("WATER!", None))
    assert baseline._keep_task(baseline.module, task)


def test_live_water_cohorts_finish():
    _, _, statuses = run_match(str(CANDIDATE), BASELINE, seed=42)
    assert statuses == ["DONE", "DONE"]

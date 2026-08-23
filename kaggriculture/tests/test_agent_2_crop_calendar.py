import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))

from runner import load_agent, run_match
from variants import _environment


ROOT = pathlib.Path(__file__).resolve().parent.parent
CANDIDATE = ROOT / "agents_2.0.x/round42_5_crop_calendar"
CONTROL = ROOT / "agents_2.0.x/round42_1_dynamic_herd"


def _policy():
    loaded = load_agent(str(CANDIDATE))
    return loaded.module._policy_agent.policy.baseline


def _inputs(count=20):
    tiles = [(index % 10, index // 10, None) for index in range(count)]
    result = {(x, y): None for x, y, _tile in tiles}
    inventory = {
        "WHEAT": 9000,
        "CARROT": 11000,
        "TOMATO": 10000,
        "STRAWBERRY": 10000,
        "MELON": 10000,
    }
    return tiles, result, inventory


def test_crop_calendar_is_bounded_and_demand_conditioned():
    values = {
        "AGENT2_CROP_CALENDAR": "1",
        "AGENT2_CROP_CALENDAR_COHORT": "7",
    }
    with _environment(values):
        policy = _policy()
        tiles, result, inventory = _inputs()
        policy._apply_crop_calendar(
            policy.module, result, tiles, 18, inventory, [], 10,
        )
    planted = [crop for crop in result.values() if crop]
    assert len(planted) == 7
    assert set(planted) <= {"WHEAT", "CARROT"}
    assert planted.count("WHEAT") > planted.count("CARROT")


def test_crop_calendar_does_not_refill_completed_cohort():
    values = {
        "AGENT2_CROP_CALENDAR": "1",
        "AGENT2_CROP_CALENDAR_COHORT": "3",
    }
    with _environment(values):
        policy = _policy()
        tiles, result, inventory = _inputs()
        policy._apply_crop_calendar(
            policy.module, result, tiles, 18, inventory, [], 10,
        )
        cohort = dict(policy._late_crop_cohort)
        growing = [
            (x, y, {"kind": "PLANT", "crop": cohort.get((x, y), "MELON")})
            for x, y, _tile in tiles
        ]
        policy._apply_crop_calendar(
            policy.module, result, growing, 19, inventory, [], 10,
        )
        empty, refill, _inventory = _inputs()
        for position in cohort:
            refill[position] = "CARROT"
        policy._apply_crop_calendar(
            policy.module, refill, empty, 23, inventory, [], 10,
        )
    assert all(refill[position] is None for position in cohort)


def test_crop_calendar_rejects_cohort_after_sale_deadline():
    with _environment({"AGENT2_CROP_CALENDAR": "1"}):
        policy = _policy()
        tiles, result, inventory = _inputs()
        policy._apply_crop_calendar(
            policy.module, result, tiles, 26, inventory, [], 10,
        )
    assert policy._late_crop_cohort == {}
    assert all(crop is None for crop in result.values())


def test_crop_calendar_finishes_live_episode(monkeypatch):
    monkeypatch.setenv("AGENT2_DYNAMIC_HERD", "1")
    monkeypatch.setenv("AGENT2_CROP_CALENDAR", "1")
    _, _, statuses = run_match(str(CANDIDATE), str(CONTROL), seed=67)
    assert statuses == ["DONE", "DONE"]

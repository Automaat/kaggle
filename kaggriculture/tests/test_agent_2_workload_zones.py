import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))

from runner import load_agent, run_match


ROOT = pathlib.Path(__file__).resolve().parent.parent
CANDIDATE = ROOT / "agents_2.0.x/round37_8_workload_zones"
BASELINE = "variant:KAGG_LAND=2;KAGG_MAX_HANDS=14;KAGG_HANDS_PER_TILE=0.2"


def test_animal_tiles_receive_more_zone_capacity():
    loaded = load_agent(str(CANDIDATE))
    module = loaded.module._policy_agent.policy.baseline.module
    tiles = [
        (0, 0, {"kind": "PLANT"}),
        (1, 0, {"kind": "PASTURE", "animal": "COW"}),
        (2, 0, {"kind": "PLANT"}),
        (3, 0, {"kind": "PLANT"}),
    ]
    zones = module._cluster_plan(0, 1, 24, tiles, 2, 10)
    assert zones[(0, 0)] == 0
    assert zones[(1, 0)] == 0
    assert zones[(2, 0)] == 1
    assert zones[(3, 0)] == 1


def test_workload_zones_finish_live_episode():
    _, _, statuses = run_match(str(CANDIDATE), BASELINE, seed=42)
    assert statuses == ["DONE", "DONE"]
